"""LOCAL TEST ONLY: run MuseTalk 1.5 inference on a CPU box with ~2.5 GB RAM.
Builds the UNet with empty weights, assigns mmap'd weights cast to bfloat16,
and casts UNet/VAE inputs. Same CLI as scripts.inference (do not pass --use_float16).
Usage (cwd = MuseTalk repo): python cpu_lowmem_musetalk.py <scripts.inference args>"""
import json, sys, runpy, torch
from accelerate import init_empty_weights
import musetalk.models.unet as U
from diffusers import UNet2DConditionModel
DT = torch.bfloat16

def _init(self, unet_config, model_path, use_float16=False, device=None):
    cfg = json.load(open(unet_config))
    with init_empty_weights():
        m = UNet2DConditionModel(**cfg)
    sd = torch.load(model_path, map_location="cpu", mmap=True, weights_only=True)
    m.load_state_dict({k: v.to(DT) for k, v in sd.items()}, assign=True)
    del sd
    m = m.to(DT).eval()
    fwd = m.forward
    m.forward = lambda x, t, encoder_hidden_states=None, **kw: fwd(x.to(DT), t, encoder_hidden_states=encoder_hidden_states.to(DT), **kw)
    self.model, self.device = m, torch.device("cpu")
    self.pe = U.PositionalEncoding(d_model=384)
U.UNet.__init__ = _init

import musetalk.models.vae as V
_dec = V.VAE.decode_latents
def _decode(self, latents):
    return _dec(self, latents.float())
V.VAE.decode_latents = _decode
torch.set_num_threads(6)
sys.argv = ["scripts/inference.py"] + sys.argv[1:]
runpy.run_module("scripts.inference", run_name="__main__")
