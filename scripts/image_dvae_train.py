"""
Train a diffusion model on images.
"""

import argparse

from guided_diffusion import dist_util, logger
from guided_diffusion.image_datasets import load_data
from guided_diffusion.resample import create_named_schedule_sampler
from guided_diffusion.script_util import (
    model_and_diffusion_defaults,
    create_model_and_diffusion,
    args_to_dict,
    add_dict_to_argparser,
)
from guided_diffusion.train_util import TrainLoop

from dalle_pytorch import DiscreteVAE
from einops import rearrange

import torch

def set_requires_grad(model, value):
    for param in model.parameters():
        param.requires_grad = value

def main():
    args = create_argparser().parse_args()

    dist_util.setup_dist()
    logger.configure()

    logger.log("setting up DALLE-pytorch dvae...")

    loaded_obj = torch.load('./models/dvae/vae-classifier-128-8192.pt')

    vae_params, vae_weights = loaded_obj['hparams'], loaded_obj['weights']

    vae = DiscreteVAE(**vae_params)
    vae.load_state_dict(vae_weights, strict=False)
    vae = vae.to(dist_util.dev())
    set_requires_grad(vae, False) # freeze VAE from being trained

    logger.log("creating model and diffusion...")
    model, diffusion = create_model_and_diffusion(
        **args_to_dict(args, model_and_diffusion_defaults().keys())
    )

    logger.log('args: ', args_to_dict(args, model_and_diffusion_defaults().keys()))
    model.to(dist_util.dev())
    schedule_sampler = create_named_schedule_sampler(args.schedule_sampler, diffusion)

    logger.log("creating data loader...")
    data = load_data_custom(
        vae = vae,
        data_dir=args.data_dir,
        batch_size=args.batch_size,
        image_size=args.image_size,
        class_cond=args.class_cond,
    )

    logger.log("training...")
    TrainLoop(
        model=model,
        diffusion=diffusion,
        data=data,
        batch_size=args.batch_size,
        microbatch=args.microbatch,
        lr=args.lr,
        ema_rate=args.ema_rate,
        log_interval=args.log_interval,
        save_interval=args.save_interval,
        resume_checkpoint=args.resume_checkpoint,
        use_fp16=args.use_fp16,
        fp16_scale_growth=args.fp16_scale_growth,
        schedule_sampler=schedule_sampler,
        weight_decay=args.weight_decay,
        lr_anneal_steps=args.lr_anneal_steps,
        lr_warmup_steps=args.lr_warmup_steps,
    ).run_loop()


def create_argparser():
    defaults = dict(
        data_dir="",
        schedule_sampler="uniform",
        lr=1e-4,
        weight_decay=0.0,
        lr_anneal_steps=0,
        lr_warmup_steps=0,
        batch_size=1,
        microbatch=-1,  # -1 disables microbatches
        ema_rate="0.9999",  # comma-separated list of EMA values
        log_interval=10,
        save_interval=10000,
        resume_checkpoint="",
        use_fp16=False,
        fp16_scale_growth=1e-3,
        emb_condition=True,
    )
    defaults.update(model_and_diffusion_defaults())
    parser = argparse.ArgumentParser()
    add_dict_to_argparser(parser, defaults)
    return parser


def load_data_custom(vae, data_dir, batch_size, image_size, class_cond=False):
    data = load_data(
        data_dir=data_dir,
        batch_size=batch_size,
        image_size=image_size,
        class_cond=class_cond,
        emb_condition=True
    )
    for large_batch, model_kwargs in data:
        arr_tens = model_kwargs["image_128"].to(dist_util.dev())

        img_seq = vae.get_codebook_indices(arr_tens)
        image_embeds = self.vae.codebook(img_seq)
        b, n, d = image_embeds.shape
        h = w = int(math.sqrt(n))

        image_embeds = rearrange(image_embeds, 'b (h w) d -> b d h w', h = h, w = w)

        model_kwargs["image_embeds"] = image_embeds.detach().cpu()

        del model_kwargs["image_128"]
        yield large_batch, model_kwargs

if __name__ == "__main__":
    main()
