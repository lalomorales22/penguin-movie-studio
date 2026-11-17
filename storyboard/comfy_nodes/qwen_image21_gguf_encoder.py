"""Loader shim: use a text-only Qwen3-VL-8B GGUF as the Qwen-Image 2.1 text encoder.

ComfyUI recognises the Qwen-Image 2.1 encoder by the vision-tower weights that ship
inside the official safetensors file. A llama.cpp-style GGUF holds only the language
model (the vision part lives in a separate mmproj file), so ComfyUI mistakes it for a
plain Qwen3 text model and picks the wrong prompt template.

This node loads the GGUF through the ComfyUI-GGUF add-on while telling ComfyUI
"this is Qwen3-VL-8B". Text-to-image never touches the vision tower, so its weights
are simply left unloaded. Image *editing* would need them and is not supported here.

Requires the ComfyUI-GGUF add-on (custom_nodes/ComfyUI-GGUF).
"""
import comfy.sd
import nodes


class CLIPLoaderGGUFQwenImage21:
    @classmethod
    def INPUT_TYPES(s):
        base = nodes.NODE_CLASS_MAPPINGS.get("CLIPLoaderGGUF")
        files = [f for f in base.get_filename_list() if f.lower().endswith(".gguf")] if base else []
        return {"required": {"clip_name": (files,)}}

    RETURN_TYPES = ("CLIP",)
    FUNCTION = "load_clip"
    CATEGORY = "loaders"

    def load_clip(self, clip_name):
        base = nodes.NODE_CLASS_MAPPINGS.get("CLIPLoaderGGUF")
        if base is None:
            raise RuntimeError("The ComfyUI-GGUF add-on is not installed (custom_nodes/ComfyUI-GGUF).")
        original = comfy.sd.detect_te_model
        comfy.sd.detect_te_model = lambda sd: comfy.sd.TEModel.QWEN3VL_8B
        try:
            return base().load_clip(clip_name, type="qwen_image")
        finally:
            comfy.sd.detect_te_model = original


NODE_CLASS_MAPPINGS = {"CLIPLoaderGGUFQwenImage21": CLIPLoaderGGUFQwenImage21}
NODE_DISPLAY_NAME_MAPPINGS = {"CLIPLoaderGGUFQwenImage21": "Qwen-Image 2.1 Text Encoder (GGUF)"}
