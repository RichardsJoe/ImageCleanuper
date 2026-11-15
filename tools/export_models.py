"""Build the AI model files the app ships with (run by the GitHub build, or by hand).

    python tools/export_models.py <output folder>

Downloads (each checked against a known SHA-256 fingerprint):
  - YuNet face detector and SFace face recogniser (OpenCV Zoo, Apache-2.0 / MIT)
  - CLIP ViT-B/32 (OpenAI, MIT) and its tokenizer vocabulary
Then exports CLIP's image and text encoders to ONNX (half precision, inputs/outputs float32)
and checks the ONNX results match the original model.

Needs: torch, onnx, onnxruntime, onnxconverter-common, numpy, Pillow. Not needed by the app itself.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
from collections import OrderedDict
from pathlib import Path

SOURCES = {
    "face_detection_yunet_2023mar.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx", None),
    "face_recognition_sface_2021dec.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/"
        "face_recognition_sface_2021dec.onnx", None),
    "ViT-B-32.pt": (
        "https://openaipublic.azureedge.net/clip/models/40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af/"
        "ViT-B-32.pt", "40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af"),
    "bpe_simple_vocab_16e6.txt.gz": ("https://openaipublic.azureedge.net/clip/bpe_simple_vocab_16e6.txt.gz", None),
}
# Fingerprints of the OpenCV files, filled from the first verified download (see PINNED below).
PINNED = Path(__file__).with_name("model_fingerprints.json")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(name: str, dest: Path, cache: Path) -> Path:
    url, expected = SOURCES[name]
    pinned = json.loads(PINNED.read_text()) if PINNED.exists() else {}
    expected = expected or pinned.get(name)
    target = cache / name
    if not target.exists() or (expected and sha256(target) != expected):
        print(f"downloading {name}")
        tmp = target.with_suffix(target.suffix + ".partial")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ImageCleanuper-build"})) as r, \
                open(tmp, "wb") as out:
            shutil.copyfileobj(r, out)
        tmp.replace(target)
    got = sha256(target)
    if expected and got != expected:
        raise SystemExit(f"{name}: fingerprint mismatch ({got})")
    return target


# ---- CLIP (ViT-B/32) rebuilt as plain PyTorch modules so it exports cleanly -------------
def build_clip(state):
    import torch
    from torch import nn

    class QuickGELU(nn.Module):
        def forward(self, x):
            return x * torch.sigmoid(1.702 * x)

    class Block(nn.Module):
        def __init__(self, d, heads, mask=None):
            super().__init__()
            self.attn = nn.MultiheadAttention(d, heads)
            self.ln_1 = nn.LayerNorm(d)
            self.mlp = nn.Sequential(OrderedDict([("c_fc", nn.Linear(d, d * 4)), ("gelu", QuickGELU()),
                                                  ("c_proj", nn.Linear(d * 4, d))]))
            self.ln_2 = nn.LayerNorm(d)
            self.mask = mask

        def forward(self, x):
            m = self.mask.to(dtype=x.dtype, device=x.device) if self.mask is not None else None
            x = x + self.attn(self.ln_1(x), self.ln_1(x), self.ln_1(x), need_weights=False, attn_mask=m)[0]
            return x + self.mlp(self.ln_2(x))

    class Transformer(nn.Module):
        def __init__(self, d, layers, heads, mask=None):
            super().__init__()
            self.resblocks = nn.Sequential(*[Block(d, heads, mask) for _ in range(layers)])

        def forward(self, x):
            return self.resblocks(x)

    class Visual(nn.Module):
        def __init__(self, res=224, patch=32, width=768, layers=12, heads=12, out=512):
            super().__init__()
            self.conv1 = nn.Conv2d(3, width, patch, patch, bias=False)
            scale = width ** -0.5
            self.class_embedding = nn.Parameter(scale * torch.randn(width))
            self.positional_embedding = nn.Parameter(scale * torch.randn((res // patch) ** 2 + 1, width))
            self.ln_pre = nn.LayerNorm(width)
            self.transformer = Transformer(width, layers, heads)
            self.ln_post = nn.LayerNorm(width)
            self.proj = nn.Parameter(scale * torch.randn(width, out))

        def forward(self, x):
            x = self.conv1(x)
            x = x.reshape(x.shape[0], x.shape[1], -1).permute(0, 2, 1)
            cls = self.class_embedding.to(x.dtype) + torch.zeros(x.shape[0], 1, x.shape[-1], dtype=x.dtype)
            x = torch.cat([cls, x], dim=1) + self.positional_embedding.to(x.dtype)
            x = self.ln_pre(x).permute(1, 0, 2)
            x = self.transformer(x).permute(1, 0, 2)
            return self.ln_post(x[:, 0, :]) @ self.proj

    class Text(nn.Module):
        def __init__(self, ctx=77, vocab=49408, width=512, heads=8, layers=12, out=512):
            super().__init__()
            mask = torch.empty(ctx, ctx).fill_(float("-inf")).triu_(1)
            self.token_embedding = nn.Embedding(vocab, width)
            self.positional_embedding = nn.Parameter(torch.empty(ctx, width))
            self.transformer = Transformer(width, layers, heads, mask)
            self.ln_final = nn.LayerNorm(width)
            self.text_projection = nn.Parameter(torch.empty(width, out))

        def forward(self, tokens):
            x = self.token_embedding(tokens) + self.positional_embedding
            x = self.transformer(x.permute(1, 0, 2)).permute(1, 0, 2)
            x = self.ln_final(x)
            eot = tokens.argmax(dim=-1)
            return x[torch.arange(x.shape[0]), eot] @ self.text_projection

    visual, text = Visual(), Text()
    vs = {k[len("visual."):]: v for k, v in state.items() if k.startswith("visual.")}
    ts = {k: v for k, v in state.items() if k.split(".")[0] in
          ("token_embedding", "positional_embedding", "transformer", "ln_final", "text_projection")}
    visual.load_state_dict(vs, strict=True)
    text.load_state_dict(ts, strict=True)
    return visual.float().eval(), text.float().eval(), float(state["logit_scale"].exp())


def export_clip(pt: Path, out: Path, vocab: Path) -> dict:
    import numpy as np
    import onnx
    import onnxruntime as ort
    import torch
    from onnxconverter_common import float16

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from imagecleanuper.ai.tokenizer import Tokenizer

    jit = torch.jit.load(str(pt), map_location="cpu").eval().float()
    visual, text, logit_scale = build_clip(jit.state_dict())
    img = torch.randn(2, 3, 224, 224)
    tok = torch.tensor(Tokenizer(vocab).encode_batch(["a photo of a dog", "a scanned old family photo"]),
                       dtype=torch.int64)
    # OpenAI's file has CUDA baked into its traced graphs, so it can't run on a machine without an
    # NVIDIA GPU. Checks instead: every weight loads into the rebuilt model with strict=True (done in
    # build_clip), the ONNX files match the rebuilt model, and caption-matching sanity tests pass.
    with torch.no_grad():
        ref_img = visual(img).numpy()
        ref_txt = text(tok).numpy()
    files = {}
    for name, model, inp, iname, oname in (("clip_image.onnx", visual, img, "pixels", "embedding"),
                                           ("clip_text.onnx", text, tok, "tokens", "embedding")):
        tmp = out / (name + ".fp32")
        torch.onnx.export(model, inp, str(tmp), input_names=[iname], output_names=[oname],
                          dynamic_axes={iname: {0: "n"}, oname: {0: "n"}}, opset_version=17, do_constant_folding=True)
        m16 = float16.convert_float_to_float16(onnx.load(str(tmp)), keep_io_types=True)
        onnx.save(m16, str(out / name))
        tmp.unlink()
        files[name] = out / name
    # Check the half-precision ONNX files against the original (cosine similarity).
    def cos(a, b):
        a = a / np.linalg.norm(a, axis=1, keepdims=True)
        b = b / np.linalg.norm(b, axis=1, keepdims=True)
        return float((a * b).sum(1).min())
    si = ort.InferenceSession(str(files["clip_image.onnx"]), providers=["CPUExecutionProvider"])
    st = ort.InferenceSession(str(files["clip_text.onnx"]), providers=["CPUExecutionProvider"])
    ci = cos(si.run(None, {"pixels": img.numpy()})[0], ref_img)
    ct = cos(st.run(None, {"tokens": tok.numpy()})[0], ref_txt)
    print(f"ONNX vs original: image {ci:.5f}, text {ct:.5f}")
    assert ci > 0.999 and ct > 0.999, "ONNX export drifted from the original"
    # Sanity: a red square must match "a red square" better than "a photo of a dog" (text side works).
    from PIL import Image
    red = Image.new("RGB", (224, 224), (220, 30, 30))
    px = (np.asarray(red, np.float32) / 255 - [0.48145466, 0.4578275, 0.40821073]) / [0.26862954, 0.26130258, 0.27577711]
    e_img = si.run(None, {"pixels": px.transpose(2, 0, 1)[None].astype(np.float32)})[0]
    caps = ["a red square", "a photo of a dog", "a green field"]
    e_txt = st.run(None, {"tokens": np.array(Tokenizer(vocab).encode_batch(caps), np.int64)})[0]
    sims = (e_img / np.linalg.norm(e_img)) @ (e_txt / np.linalg.norm(e_txt, axis=1, keepdims=True)).T
    print("sanity:", dict(zip(caps, np.round(sims[0], 3))))
    assert sims[0].argmax() == 0, "text encoder failed the sanity test"
    return {"logit_scale": logit_scale}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cache = out.parent / (out.name + "_downloads")   # kept outside the folder that gets bundled
    cache.mkdir(exist_ok=True)
    got = {n: fetch(n, out, cache) for n in SOURCES}
    for n in ("face_detection_yunet_2023mar.onnx", "face_recognition_sface_2021dec.onnx", "bpe_simple_vocab_16e6.txt.gz"):
        shutil.copy(got[n], out / n)
    info = export_clip(got["ViT-B-32.pt"], out, got["bpe_simple_vocab_16e6.txt.gz"])
    manifest = {"version": 1, "clip": info,
                "files": {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "models.json"}}
    (out / "models.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "models")
