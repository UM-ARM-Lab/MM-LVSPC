import numpy as np
from gym import utils
from gym.envs.mujoco import mujoco_env
import os


# TODO: find a way of doing stronger domain rand on ball images
class MujocoBall(mujoco_env.MujocoEnv, utils.EzPickle):
    def __init__(self):
        xml_path = os.path.abspath('gym_cenvs/assets/ball.xml')

        # - - - - - Init Attributes Prior since some needed for model creation - - - - - -
        # Episode/task of a contiguous trajectory complete?
        self.done = False
        # Fixed initial ball radius
        self.bradius = 0.2
        self.bradius_max_add = 0.25
        self.bradius_max_sub = 0.125
        # Domain rand over geometry
        self.randomize = True
        # - - - - - - - - - - - - - - - -
        mujoco_env.MujocoEnv.__init__(self, xml_path, frame_skip=1)
        utils.EzPickle.__init__(self)
        # Must come after model init
        self.reset_model()

    def randomize_geometry(self):
        # zero state
        self.set_state(self.init_qpos, self.init_qvel)
        # Randomize changes via uniform delta perturbations
        ball_dradius = self.np_random.uniform(low=-self.bradius_max_sub, high=self.bradius_max_add)
        self.sim.model.geom_size[self.sim.model.geom_name2id('gball')] = [self.bradius + ball_dradius,
                                                                          self.bradius + ball_dradius, 0.0]

    def step(self, action: float):
        # Unactuated freely falling ball model
        action = 0.0
        self.do_simulation(action, self.frame_skip)
        ob = self._get_obs()
        state = self._get_state()
        # Terminate if the ball goes out of view
        out_of_view_x = np.abs(self.sim.data.qpos[0]) > 2.5 # Earlier tried 2.5
        # On sided ineq since always falls down
        out_of_view_z = self.sim.data.qpos[2] < -2.5
        out_of_view = out_of_view_x or out_of_view_z
        # self.done is never set to True since there is no task
        done = out_of_view or self.done
        # dummy cost
        cost = 0.0
        return ob, -cost, done, {'success': self.done, 'state': state}

    def _get_obs(self):
        return self.render(mode='rgb_array', width=64, height=64, camera_id=0)

    # State is [x_ball, z_ball]
    def _get_state(self):
        # x_ball, z_ball, Give perception only coordinates and not velocities
        _st = np.hstack((-1 * self.sim.data.qpos[0], self.sim.data.qpos[2]))
        return _st

    def reset(self):
        self.done = False
        if self.randomize:
            self.randomize_geometry()
        return self.reset_model()

    def reset_model(self):
        # No variation in y-position (depth)
        ball_x = self.np_random.uniform(low=-1.0, high=1.0)
        ball_y = 0.0
        ball_z = self.np_random.uniform(low=-1.0, high=1.0)
        ball_xyz = np.array([ball_x, ball_y, ball_z])
        # Sphere orientation does not matter
        ball_quat = np.hstack((1.0, np.zeros(3, dtype=np.float64)))
        ball_free_jnt_state = np.hstack((ball_xyz, ball_quat))
        # Reset ball velocity randomly in (x, y) dir and 0 for z and rotational
        ball_vx = self.np_random.uniform(low=-5.0, high=5.0)
        ball_vy = 0.0
        ball_vz = self.np_random.uniform(low=-1.0, high=1.0)
        ball_vxyz = np.array([ball_vx, ball_vy, ball_vz])
        # Set ball free joint velocity (aka ball velocity) with angular terms = 0
        ball_free_jnt_vel = np.hstack((ball_vxyz, np.zeros(3, dtype=np.float64)))
        self.set_state(ball_free_jnt_state, ball_free_jnt_vel)
        return self._get_obs()

    # To be kept the same across simulated environments
    def viewer_setup(self):
        v = self.viewer
        v.cam.trackbodyid = 0
        v.cam.distance = self.model.stat.extent * 0.5
        v.cam.lookat[2] = 0.12250000000000005  # v.model.stat.center[2]
