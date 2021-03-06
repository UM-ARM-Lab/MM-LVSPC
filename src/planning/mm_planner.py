# Adapted from pytorch-mppi: https://github.com/UM-ARM-Lab/pytorch_mppi
import torch
import time
import logging
from torch.distributions.multivariate_normal import MultivariateNormal

logger = logging.getLogger(__name__)


# Implements the rescaling of weights in MPPI paper
def _ensure_non_zero(cost, beta, factor):
    return torch.exp(-factor * (cost - beta))


class MMplanner:
    """
    The Multiple Nominal Dynamics Priors Equivalent of the usual MPPI algorithm

    Designed to be aware of uncertainty while planning
    """
    def __init__(self,
                 joint_trans_model,
                 trajectory_cost,
                 nx,
                 noise_sigma,
                 num_samples=100,
                 horizon=15,
                 device="cpu",
                 lambda_=1.,
                 noise_mu=None,
                 u_min=None,
                 u_max=None,
                 u_init=None,
                 U_init=None,
                 u_scale=1,
                 u_per_command=1,
                 step_dependent_dynamics=False,
                 sample_null_action=False,
                 noise_abs_cost=False):
        """
        :param joint_trans_model: function(state, action, model_idx m) -> next_state (K x nx) taking in batch state (K x nx) and action (K x nu)
        Where model_idx is the model used to transition from the current state to the next state
        :param trajectory_cost:
        :param nx:
        :param noise_sigma:
        :param num_samples:
        :param horizon:
        :param device:
        :param lambda_:
        :param noise_mu:
        :param u_min:
        :param u_max:
        :param u_init:
        :param U_init:
        :param u_scale:
        :param u_per_command:
        :param step_dependent_dynamics:
        :param sample_null_action:
        :param noise_abs_cost:
        """


class MPPI():
    """
    Model Predictive Path Integral control
    This implementation batch samples the trajectories and so scales well with the number of samples K.

    Implemented according to algorithm 2 in Williams et al., 2017
    'Information Theoretic MPC for Model-Based Reinforcement Learning',
    based off of https://github.com/ferreirafabio/mppi_pendulum
    """

    def __init__(self,
                 dynamics,
                 trajectory_cost,
                 nx,
                 noise_sigma,
                 num_samples=100,
                 horizon=15,
                 device="cpu",
                 lambda_=1.,
                 noise_mu=None,
                 u_min=None,
                 u_max=None,
                 u_init=None,
                 U_init=None,
                 u_scale=1,
                 u_per_command=1,
                 step_dependent_dynamics=False,
                 sample_null_action=False,
                 noise_abs_cost=False):
        """
        :param dynamics: function(state, action) -> next_state (K x nx) taking in batch state (K x nx) and action (K x nu)
        :param trajectory_cost: function(state, action) -> cost (K x 1) taking in batch state and action
        (same as dynamics). Responsible for running costs, conditional costs, other kinds of costs like uncertainty ...
        :param nx: state dimension
        :param noise_sigma: (nu x nu) control noise covariance (assume v_t ~ N(u_t, noise_sigma))
        :param num_samples: K, number of trajectories to sample
        :param horizon: T, length of each trajectory
        :param device: pytorch device
        :param lambda_: temperature, positive scalar where larger values will allow more exploration
        :param noise_mu: (nu) control noise mean (used to bias control samples); defaults to zero mean
        :param u_min: (nu) minimum values for each dimension of control to pass into dynamics
        :param u_max: (nu) maximum values for each dimension of control to pass into dynamics
        :param u_init: (nu) what to initialize new end of trajectory control to be; defeaults to zero
        :param U_init: (T x nu) initial control sequence; defaults to noise
        :param step_dependent_dynamics: whether the passed in dynamics needs horizon step passed in (as 3rd arg)
        :param sample_null_action: Whether to explicitly sample a null action (bad for starting in a local minima)
        :param noise_abs_cost: Whether to use the absolute value of the action noise to avoid bias when all states have the same cost
        """
        self.d = device
        self.dtype = noise_sigma.dtype
        self.K = num_samples  # N_SAMPLES
        self.T = horizon  # TIMESTEPS, sometimes called H

        # dimensions of state and control
        self.nx = nx
        self.nu = 1 if len(noise_sigma.shape) == 0 else noise_sigma.shape[0]
        self.lambda_ = lambda_

        if noise_mu is None:
            noise_mu = torch.zeros(self.nu, dtype=self.dtype)

        if u_init is None:
            u_init = torch.zeros_like(noise_mu)

        # handle 1D edge case
        if self.nu == 1:
            noise_mu = noise_mu.view(-1)
            noise_sigma = noise_sigma.view(-1, 1)

        # bounds
        self.u_min = u_min
        self.u_max = u_max
        self.u_scale = u_scale
        self.u_per_command = u_per_command
        # make sure if any of them is specified, both are specified
        if self.u_max is not None and self.u_min is None:
            if not torch.is_tensor(self.u_max):
                self.u_max = torch.tensor(self.u_max, dtype=self.dtype)
            self.u_min = -self.u_max
        if self.u_min is not None and self.u_max is None:
            if not torch.is_tensor(self.u_min):
                self.u_min = torch.tensor(self.u_min, dtype=self.dtype)
            self.u_max = -self.u_min
        if self.u_min is not None:
            if not torch.is_tensor(self.u_min):
                self.u_min = torch.tensor(self.u_min, dtype=self.dtype)
            if not torch.is_tensor(self.u_max):
                self.u_max = torch.tensor(self.u_max, dtype=self.dtype)
            self.u_min = self.u_min.to(device=self.d)
            self.u_max = self.u_max.to(device=self.d)

        self.noise_mu = noise_mu.to(self.d)
        self.noise_sigma = noise_sigma.to(self.d)
        self.noise_sigma_inv = torch.inverse(self.noise_sigma)
        self.noise_dist = MultivariateNormal(self.noise_mu, covariance_matrix=self.noise_sigma)
        # T x nu control sequence
        self.U = U_init
        self.u_init = u_init.to(self.d)

        if self.U is None:
            self.U = self.noise_dist.sample((self.T,))

        self.step_dependency = step_dependent_dynamics
        self.F = dynamics
        self.trajectory_cost = trajectory_cost
        self.sample_null_action = sample_null_action
        self.noise_abs_cost = noise_abs_cost
        self.state = None

        # sampled results from last command
        self.cost_total = None
        self.cost_total_non_zero = None
        self.omega = None
        self.states = None
        self.actions = None

    def _dynamics(self, state, u, t):
        return self.F(state, u, t) if self.step_dependency else self.F(state, u)

    def command(self, state):
        """
        :param state: (nx) or (K x nx) current state, or samples of states (for propagating a distribution of states)
        :returns action: (nu) best action
        """
        # shift command 1 time step
        self.U = torch.roll(self.U, -1, dims=0)
        self.U[-1] = self.u_init

        if not torch.is_tensor(state):
            state = torch.tensor(state)
        self.state = state.to(dtype=self.dtype, device=self.d)

        cost_total = self._compute_total_cost_batch()

        beta = torch.min(cost_total)
        # noinspection PyTypeChecker
        self.cost_total_non_zero = _ensure_non_zero(cost_total, beta, 1 / self.lambda_)

        eta = torch.sum(self.cost_total_non_zero)
        self.omega = (1. / eta) * self.cost_total_non_zero

        # - - - - - - - - - - - - - - - - - - - - - - -
        # Compute the planned action sequence as a weighted sum
        for t in range(self.T):
            self.U[t] += torch.sum(self.omega.view(-1, 1) * self.noise[:, t], dim=0)
        # - - - - - - - - - - - - - - - - - - - - - - -

        # Extract the number of actions agent creating controller asks for in u_per_command
        actions = self.u_scale * self.U[:self.u_per_command]

        # reduce dimensionality if we only need the first command (re-planning after every action)
        if self.u_per_command == 1:
            actions = actions[0]

        rollout = self.get_rollouts(state)

        return actions, rollout

    def reset(self):
        """
        Clear controller state after finishing a trial
        """
        self.U = self.noise_dist.sample((self.T,))

    def _compute_total_cost_batch(self):
        # parallelize sampling across trajectories
        self.cost_total = torch.zeros(self.K, device=self.d, dtype=self.dtype)

        # allow propagation of a sample of states (ex. to carry a distribution), or to start with a single state
        if self.state.shape == (self.K, self.nx):
            state = self.state
        else:
            state = self.state.view(1, -1).repeat(self.K, 1)

        # resample noise each time we take an action
        self.noise = self.noise_dist.sample((self.K, self.T))
        # broadcast own control to noise over samples; now it's K x T x nu
        self.perturbed_action = self.U + self.noise
        if self.sample_null_action:
            self.perturbed_action[self.K - 1] = 0
        # naively bound control
        # self.perturbed_action = self._bound_action(self.perturbed_action)
        self.perturbed_action = self.perturbed_action.clamp(min=self.u_min, max=self.u_max)
        # bounded noise after bounding (some got cut off, so we don't penalize that in action cost)
        self.noise = self.perturbed_action - self.U

        if self.noise_abs_cost:
            action_cost = self.lambda_ * torch.abs(self.noise) @ self.noise_sigma_inv
            # NOTE: The original paper does self.lambda_ * torch.abs(self.noise) @ self.noise_sigma_inv, but this biases
            # the actions with low noise if all states have the same cost.
            # With abs(noise) we prefer actions close to the nominal trajectory.
        else:
            action_cost = self.lambda_ * self.noise @ self.noise_sigma_inv  # Like original paper

        self.states = []
        self.actions = []

        # Recursively apply the dynamics function to get the K proposed trajectories
        for t in range(self.T):
            u = self.u_scale * self.perturbed_action[:, t]
            state = self._dynamics(state, u, t)
            # Save total states/actions
            self.states.append(state)
            self.actions.append(u)

        # Actions is K x T x nu, States is K x T x nx
        # Converts list to tensor
        self.actions = torch.stack(self.actions, dim=1)
        self.states = torch.stack(self.states, dim=1)

        if self.trajectory_cost:
            traj_cost = self.trajectory_cost(self.states, self.actions)
            self.cost_total += traj_cost
        self.actions /= self.u_scale

        # action perturbation cost
        perturbation_cost = torch.sum(self.U * action_cost, dim=(1, 2))
        self.cost_total += perturbation_cost
        return self.cost_total

    def _bound_action(self, action):
        if self.u_max is not None:
            for t in range(self.T):
                u = action[:, self._slice_control(t)]
                cu = torch.max(torch.min(u, self.u_max), self.u_min)
                action[:, self._slice_control(t)] = cu
        return action

    def _slice_control(self, t):
        return slice(t * self.nu, (t + 1) * self.nu)

    def get_rollouts(self, state, num_rollouts=1):
        """
        Return the state-sequence corresponding to the MPC planned sequence of actions ...
        Useful for debugging/visualization of planning
        :param state: either (nx) vector or (num_rollouts x nx) for sampled initial states
        :param num_rollouts: Number of rollouts with same action sequence - for generating samples with stochastic
                             dynamics
        :returns states: num_rollouts x T x nx vector of trajectories
        """
        state = state.view(-1, self.nx)
        if state.size(0) == 1:
            state = state.repeat(num_rollouts, 1)

        T = self.U.shape[0]
        states = torch.zeros((num_rollouts, T + 1, self.nx), dtype=self.U.dtype, device=self.U.device)
        states[:, 0] = state
        for t in range(T):
            states[:, t + 1] = self._dynamics(states[:, t].view(num_rollouts, -1),
                                              self.u_scale * self.U[t].view(num_rollouts, -1), t)
        # TODO: Maybe return starting from the current state
        return states[:, 1:]


def run_mppi(mppi, env, retrain_dynamics, retrain_after_iter=50, iter=1000, render=True):
    dataset = torch.zeros((retrain_after_iter, mppi.nx + mppi.nu), dtype=mppi.U.dtype, device=mppi.d)
    total_reward = 0
    for i in range(iter):
        state = env.state.copy()
        command_start = time.perf_counter()
        action = mppi.command(state)
        elapsed = time.perf_counter() - command_start
        s, r, _, _ = env.step(action.cpu().numpy())
        total_reward += r
        logger.debug("action taken: %.4f cost received: %.4f time taken: %.5fs", action, -r, elapsed)
        if render:
            env.render()

        di = i % retrain_after_iter
        if di == 0 and i > 0:
            retrain_dynamics(dataset)
            # don't have to clear dataset since it'll be overridden, but useful for debugging
            dataset.zero_()
        dataset[di, :mppi.nx] = torch.tensor(state, dtype=mppi.U.dtype)
        dataset[di, mppi.nx:] = action
    return total_reward, dataset
