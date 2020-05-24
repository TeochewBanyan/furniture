import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class CNN(nn.Module):
    def __init__(self, config, input_dim):
        super().__init__()

        self.convs = nn.ModuleList()
        d_prev = input_dim
        d = config.encoder_conv_dim
        w = config.encoder_image_size
        for k, s in zip(config.encoder_kernel_size, config.encoder_stride):
            self.convs.append(nn.Conv2d(d_prev, d, int(k), int(s)))
            w = int(np.floor((w - (int(k) - 1) - 1) / int(s) + 1))
            d_prev = d

        # screen_width == 32 (8,4)-(3,2) -> 3x3
        # screen_width == 64 (8,4)-(3,2)-(3,2) -> 3x3
        # screen_width == 128 (8,4)-(3,2)-(3,2)-(3,2) -> 3x3
        # screen_width == 256 (8,4)-(3,2)-(3,2)-(3,2) -> 7x7

        print("Output of CNN (%d) = %d x %d x %d" % (w * w * d, w, w, d))
        self.output_dim = config.encoder_conv_output_dim

        self.fc = nn.Linear(w * w * d, self.output_dim)
        self.ln = nn.LayerNorm(self.output_dim)

    def forward(self, ob, detach_cnn=False):
        out = ob
        for conv in self.convs:
            out = F.relu(conv(out))
        out = out.flatten(start_dim=1)

        if detach_cnn:
            out = out.detach()

        out = self.fc(out)
        out = self.ln(out)
        out = F.tanh(out)

        return out

    # from https://github.com/MishaLaskin/rad/blob/master/encoder.py
    def copy_conv_weights_from(self, source):
        """Tie convolutional layers"""
        # only tie conv layers
        for i, conv in enumerate(self.convs):
            tie_weights(src=source.convs[i], trg=conv)


def fanin_init(tensor):
    size = tensor.size()
    if len(size) == 2:
        fan_in = size[0]
    elif len(size) > 2:
        fan_in = np.prod(size[1:])
    else:
        raise Exception("Shape must be have dimension at least 2.")
    bound = 1.0 / np.sqrt(fan_in)
    return tensor.data.uniform_(-bound, bound)


class MLP(nn.Module):
    def __init__(
        self, config, input_dim, output_dim, hid_dims=[], activation_fn=None,
    ):
        super().__init__()
        self.activation_fn = activation_fn
        if activation_fn is None:
            self.activation_fn = getattr(F, config.rl_activation)

        self.fcs = nn.ModuleList()
        prev_dim = input_dim
        for d in hid_dims:
            self.fcs.append(nn.Linear(prev_dim, d))
            fanin_init(self.fcs[-1].weight)
            self.fcs[-1].bias.data.fill_(0.1)
            prev_dim = d
        self.fcs.append(nn.Linear(prev_dim, output_dim))
        self.fcs[-1].weight.data.uniform_(-1e-3, 1e-3)
        self.fcs[-1].bias.data.uniform_(-1e-3, 1e-3)

        self.output_dim = output_dim

    def forward(self, ob):
        out = ob
        for fc in self.fcs[:-1]:
            out = self.activation_fn(fc(out))
        out = self.fcs[-1](out)
        return out


def flatten_ob(ob: dict, ac=None):
    """
    Flattens the observation dictionary. The observation dictionary
    can either contain a single ob, or a batch of obs.
    Any images must be flattened to 1D tensors, but
    we must be careful to check if we are doing a single instance
    or batch before we flatten.

    Returns a list of dim [N x D] where N is batch size and D is sum of flattened
    dims of observations
    """
    inp = []
    images = []
    single_ob = False
    for k, v in ob.items():
        if k in ["camera_ob", "depth_ob", "segmentation_ob"]:
            images.append(v)
        else:
            if len(v.shape) == 1:
                single_ob = True
            inp.append(v)
    # concatenate images into 1D
    for image in images:
        if single_ob:
            img = torch.flatten(image)
        else:  # batch of obs, flatten after bs dim
            img = torch.flatten(image, start_dim=1)
        inp.append(img)
    # now flatten into Nx1 tensors
    if single_ob:
        inp = [x.unsqueeze(0) for x in inp]

    if ac is not None:
        ac = list(ac.values())
        if len(ac[0].shape) == 1:
            ac = [x.unsqueeze(0) for x in ac]
        inp.extend(ac)
    inp = torch.cat(inp, dim=-1)
    return inp


def flatten_ac(ac: dict):
    ac = list(ac.values())
    if len(ac[0].shape) == 1:
        ac = [x.unsqueeze(0) for x in ac]
    ac = torch.cat(ac, dim=-1)
    return ac
