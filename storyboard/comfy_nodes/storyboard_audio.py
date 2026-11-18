"""Penguin's Storyboard: turning a finished audio latent into sound.

StoryboardDecodeAudio does what ComfyUI's own "VAE Decode Audio" does, with two differences the app's sound-effect
and song graphs need:

  - On Apple Silicon ComfyUI runs the audio coder of Stable Audio 3 in the bfloat16 number format, and there its
    output is noise (measured on an M4: a recording put through the coder came back unrecognisable, 23 dB too loud;
    in float16 it came back intact, using the same memory). On such a Mac this node runs that coder in float16.
  - ComfyUI levels decoded audio by its average, so the loudest moments of a sound with sharp attacks are cut off
    when the file is written, and a soft sound is saved very quietly. This node sets the loudest moment to 70% of
    full scale instead; the app sets the final loudness itself.
"""
import torch

HALF_ON_MAC = {"SA3AudioVAE"}   # coders whose bfloat16 output is broken on Apple's GPU


class StoryboardDecodeAudio:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"samples": ("LATENT",), "vae": ("VAE",)}}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "decode"
    CATEGORY = "storyboard"

    def decode(self, samples, vae):
        latent = samples["samples"]
        if getattr(latent, "is_nested", False):
            latent = latent.unbind()[-1]
        coder = vae.first_stage_model
        if (type(coder).__name__ in HALF_ON_MAC and vae.vae_dtype == torch.bfloat16
                and getattr(vae.device, "type", str(vae.device)) == "mps"):
            coder.to(dtype=torch.float16)
            vae.vae_dtype = torch.float16
            try:
                vae.patcher.set_model_compute_dtype(torch.float16)
            except Exception:   # an older ComfyUI: the weights themselves are already cast, which is what counts
                pass
        audio = vae.decode(latent).movedim(-1, 1)
        level = torch.std(audio, dim=[1, 2], keepdim=True) * 5.0
        level[level < 1.0] = 1.0
        audio = audio / level
        peak = audio.abs().amax(dim=[1, 2], keepdim=True)
        audio = torch.where(peak > 1e-3, audio * (0.7 / peak.clamp(min=1e-3)), audio)   # the loudest moment at 70% of full scale
        rate = getattr(vae, "audio_sample_rate_output", getattr(vae, "audio_sample_rate", 44100))
        return ({"waveform": audio, "sample_rate": samples["sample_rate"] if "sample_rate" in samples else rate},)


NODE_CLASS_MAPPINGS = {"StoryboardDecodeAudio": StoryboardDecodeAudio}
NODE_DISPLAY_NAME_MAPPINGS = {"StoryboardDecodeAudio": "Decode Audio (Storyboard)"}
