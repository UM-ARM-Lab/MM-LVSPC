from src.config import CommonEncConfig
import torch


class Config(CommonEncConfig):
    def __init__(self, data_folder: str = None):
        super(Config, self).__init__(data_folder)

        # Dim of state returned by gym environment
        self.state_dimension = 2

        # Number of position only states
        self.nqpos = 2

        if data_folder is not None:
            # Number of observable states out of state_dims
            #  Refer to state returned by gym env
            #  The quantities that are observable depend on the number of
            # consecutive simple model frames state encoder is trained with
            if self.nframes == 1:
                self.observation_dimension = 2
            else:
                self.observation_dimension = 4

            self.action_dimension = 1

        # Training
        self.epochs = 80
        self.batch_size = 64
        self.lr_init = 3e-3
        self.lr_decay_rate = 0.1
        self.lr_decay_steps = 20
        self.optimiser = 'adam'
