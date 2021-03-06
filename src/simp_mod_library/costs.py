"""
Module contains cost functions for planning with a simple model dynamics prior
The cost functions assume that the goal region (in some abstract sense) is known as a simple model state
"""
import torch
from torch import nn


# TODO: A single MMCost entity may not be needed if changing the way planning is performed
#  But for initial experimentation, the standard MPPI code is being used so this is needed
# Options for implementing: Apply cost to every [:, idx, nx] slice of states
#  starting from the end of trajectories
# OR
# Compute the [:, idx, nx] slices for all costs and linear combine them at the end using
#  point-wise products with masks
class MMCost(nn.Module):
    """
    MMCost = Combined cost function for multiple models that looks up the appropriate
    cost function based on the model being invoked to make the transition
    """
    def __init__(self, simp_mod_lib):
        super(MMCost, self).__init__()

        # Build dynamics list and infer attributes
        for idx in range(simp_mod_lib.nmodels):
            model_cost = simp_mod_lib[idx].cost_fn

    def forward(self, states, actions):
        """
        The callable that returns the cost on states and actions for the planner to do planning
        :param states:
        :param actions:
        :return:
        """


class BallCost(nn.Module):
    """
    Ball cost is distance between Ball center and a goal location (example a moving cup)
    """
    def __init__(self):
        super(BallCost, self).__init__()

    # TODO: Add something that checks for collisions based on distance between Ball and Cup
    def forward(self):


class CartpoleTipCost(nn.Module):
    """
    Cost is euclidean distance of cartpole end effector from some goal position in R^2
    TODO: Goal position in this class definition is fixed, change to
    """
    def __init__(self, goal, l=1.):
        super(CartpoleTipCost, self).__init__()

        self.goal = goal
        self.l = l
        self.uncertainty_cost = 0.0
        self.iter = 0.0

    def set_goal(self, goal):
        self.goal = goal

    def set_max_std(self, max_std):
        self.max_std = max_std

    def check_rope_collision(self, state):
        """
        Allows for imposing cost on undesirable collisions
        :param state:
        :return:
        """
        N, T, _ = state.shape

        zeros = torch.zeros(N, T, device=state.device)
        ones = torch.ones(N, T, device=state.device)

        base_x = state[:, :, 0]
        tip_x = state[:, :, 1]
        tip_y = state[:, :, 2]
        rope_theta = torch.atan2(tip_y, tip_x - base_x)
        goal_theta = torch.atan2(torch.tensor(self.goal[1], device=state.device),
                                 self.goal[0] - base_x)
        # If approx same theta, then there may be a collision
        collision = torch.where(torch.abs(rope_theta - goal_theta) < 0.2, ones, zeros)

        if N == 1:
            print('angle check')
            print(collision)

        # If the length is greater than the length of the rope (minus the mass), then no collision
        length = torch.sqrt((tip_x - base_x)**2 + tip_y**2)
        base_to_target_d = torch.sqrt((self.goal[0] - base_x)**2 + (self.goal[1])**2)

        # When goal is further than length, clearly rope cannot collide
        collision = torch.where(base_to_target_d > 0.9 * length, zeros, collision)
        if N == 1:
            print('length check')
            print(collision)

        # When base to target id is too low
        collision = torch.where(base_to_target_d < 0.2, ones, collision)
        if N == 1:
            print('base check')
            print(collision)

        return collision

    def forward(self, state, actions=None, verbose=False):
        N, T, _ = state.shape
        base_x = state[:, :, 0]
        tip_x = state[:, :, 1]
        tip_y = state[:, :, 2]

        uncertainty = state[:, :, 5:]

        # Target cost -- 0 if goal reached in horizon, else it is distance of the end state from goal
        dist_2_goal = torch.sqrt((self.goal[0] - tip_x) ** 2 + (self.goal[1] - tip_y) ** 2)

        collisions = self.check_rope_collision(state)

        if N == 1:
            print(collisions)

        # Get components where this is zero
        at_target = (dist_2_goal < 0.1).nonzero()
        in_collision = (collisions == 1).nonzero()

        from_centre_cost = (base_x).clamp(min=1.5) - 1.5
        vel_cost = state[:, :, 4:5].abs().sum(dim=2)
        uncertainty_cost = uncertainty.clone() ** 2

        for t in range(T - 1, -1, -1):
            # count down
            at_target_idx = (at_target[:, 1] == t).nonzero()
            at_target_t = at_target[at_target_idx, 0]

            in_collision_idx = (in_collision[:, 1] == t).nonzero()
            in_collision_t = in_collision[in_collision_idx, 0]

            collisions[at_target_t, t + 1:] = 0
            dist_2_goal[at_target_t, t + 1:] = 0
            dist_2_goal[in_collision_t, t:] = 10 * 0.9**(t)
            #uncertainty_cost[in_collision_t, t:] = uncertainty_cost[in_collision_t, t - 1].unsqueeze(2)
            #uncertainty_cost[at_target_t, t + 1:] = 0


        if N == 1:
            print(dist_2_goal)
        alphas = torch.arange(0, T, device=state.device)
        gammas = torch.pow(torch.tensor(.9, device=state.device), alphas)
        from_centre_cost *= gammas
        cost = dist_2_goal + 10.0 * from_centre_cost + 1e-5 * vel_cost# + 100 * collision_cost

        #uncertainty_cost = uncertainty_cost * gammas.view(1, T)
        uncertainty_cost = uncertainty_cost @ torch.tensor([0.01, 0.01, 0.01, 0.01, 0.01], device=uncertainty_cost.device).unsqueeze(1)
        uncertainty_cost = uncertainty_cost.sum(dim=1).squeeze(1)
        uncertainty_cost = uncertainty_cost - uncertainty_cost.mean()

        uncertainty_weight = 0.2 * self.iter

        return cost.sum(dim=1) + uncertainty_weight * uncertainty_cost


# TODO: Add a cartpole cost function that makes it swing up and reach the point with low speed for slackness to follow
# TODO: Add a Ball cost function that makes Ball go into cup by aiming for robot position
