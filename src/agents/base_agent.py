from abc import ABCMeta
import gym
import torch

import gym_cenvs
import numpy as np
from src.simp_mod_library.simp_mod_lib import SimpModLib
from typing import List


class BaseAgent(metaclass=ABCMeta):
    """
    Base class for constructing agents that control the complex object using passed Simple Model Library
    """
    def __init__(self, smodel_list: List[str], device: str = 'cuda:0'):
        # String that is set in derived class
        self.env_name: str = None
        # Ref to step-able mujoco-gym environment
        self.env = None

        # Devices
        self.device = device

        # Dimensionality of an action vector for env
        self.action_dimension: int = None
        # Number of times we repeat the same action (action repetition, task specific)
        self.actions_per_loop: int = None
        # Dimensionality of ground struth state for an env
        self.state_dimension: int = None
        # Episode horizon set for task
        self.episode_T: int = None

        self.model_lib = SimpModLib(smodel_list, device)

        # Keys for the online collected dataset. gt = Ground Truth, ep = episode,
        # gt_frame = raw unprocessed frame
        # all_ep_rollouts = potentially hybrid multiple model roll-outs generated by
        #  propagating the final planned actions
        self.data_keys = ["action_history",
                          "gt_state_history",
                          "gt_frame_history",
                          "rollouts"]

        # Container for the episode data collected that is global across agent
        self.episode_data = dict()
        # Initialize all the episode-specific datasets with empty lists
        for data_key in self.data_keys:
            self.episode_data[data_key] = []

        # Vars to track agent specific objects
        self.gt_state: torch.Tensor = None

        # Special indices within gt_state that are passed down to SML
        self.obs_gt_idx: List[int] = None

    def make_agent_for_task(self):
        """
        Invoked after task specific params have been set in derived class
        :return:
        """
        self.env = gym.make(self.env_name)
        self.env.seed(0)
        self.env.action_space.seed(0)

        # TODO: Check consistency of action dimension with simple model lib here

        self.gt_state = torch.zeros(self.state_dimension, dtype=torch.float32)

        return

    @classmethod
    def __new__(cls, *args, **kwargs):
        """
        Make abstract base class non-instaiable
        :param args:
        :param kwargs:
        """
        if cls is BaseAgent:
            raise TypeError(f"only children of '{cls.__name__}' may be instantiated")
        return object.__new__(cls)

    def do_episode(self):
        """
        Agent method to online interact with the complex env over an episode
         While doing an episode, there are no parameter updates, only the datasets are appended
        :return:
        """
        # Ensure no parameter updates (also less memory since no grads are tracked)
        with torch.no_grad():
            while True:
                # Clear episode specific state
                self.reset_episode()
                # Reward accumulated over course of episode
                cum_reward = 0.0
                for t in range(0, self.episode_T, self.actions_per_loop):
                    done, fail, reward, info = self.step()
                    cum_reward += reward

                    if done or fail:
                        break
                # Let an episode drag on for 5 steps even when done ... ?
                # TODO: determine if above is necessary
                if t < 5:
                    continue
                break
            fail = not info['success']

        return fail, t

    def step(self):
        """
        Step through the environment with a task specific agent
        :return:
        """
        # TODO: Change random actions to planned actions from controller
        # Random action for testing
        action = np.random.uniform(-1, 1)
        actions = torch.tensor([[action]])

        # Total reward seen so far
        total_reward = 0

        # TODO: Loop over possibly longer sequence of actions instead of single action

        # Invoke predict method of underlying simple models
        self.model_lib.predict(actions)
        # Take the action in world and get observation
        obs, rew, done, info = self.env.step(actions[0, 0])

        gt_state = info['state']
        total_reward += rew

        # Get simple model state and uncertainty estimates for all models in lib after observation update
        z_mu_list = self.model_lib.update(obs)

        # Log data from step
        self.episode_data['gt_state_history'].append(gt_state)

        return done, False, total_reward, info

    def reset_episode(self):
        """
        Reset a single episode of interaction with the environment to move on to the next episode
         within the same trial
        :return:
        """
        if self.env is None:
            raise ValueError("Environment has not been made ... cannot reset episode...")

        for data_key in self.data_keys:
            self.episode_data[data_key] = []

        # Frame returned initially from mjco-py environment has a jump from subsequent so ignore it
        _ = self.env.reset()
        obs, rew, done, info = self.env.step(np.zeros(self.action_dimension))
        # Fetch gt_state from info
        gt_state = info['state']

        # Reset the episode-specific params of the simple model library
        #  Particularly reinit state, also uses obs to initialize simple-model state estimates for planning
        self.model_lib.reset_episode(obs)

        # Append GT state of complex environment to online collected data
        self.episode_data['gt_state_history'].append(gt_state)

        # TODO: Reset cost functions once planning is added back

    def reset_trial(self):
        """
        Reset an entire trial for a clean/fresh evaluation of MM-LVSPC
        :return:
        """
        self.model_lib.reset_trial()
