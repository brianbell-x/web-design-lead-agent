# Qwen Image Layered — Decomposition Research (2026-06-11)

## Executive summary

1. **The capability is real, not marketing**: Qwen-Image-Layered (released 2025-12-17, Apache-2.0) decomposes one RGB image into N true RGBA layers with alpha — occluded content is inpainted, PSD-like, not just edited images.
2. **Callable today** from a US dev with a card on **fal.ai** (`fal-ai/qwen-image-layered`, $0.05/decomposition) and **Replicate** (`qwen/qwen-image-layered`, ~$0.03/run). NOT on Alibaba's own DashScope/Model Studio (either region) and NOT on OpenRouter.
3. **Best provider: fal.ai** — flat $0.05 regardless of layer count (1–10 layers), PNG layer URLs back in 15–30s, trivial signup.
4. **Big caveat for mockups**: works at ~640–1024 px and *regenerates* layers via diffusion — small UI text gets mushy, layer splits are not controllable, backgrounds keep shadow remnants.
5. **Recommendation**: pipeline should be flat-mockup → image-to-code (already in workspace); decomposition is only worth a $5 fal.ai proof as an *asset-extraction assist* for logo/hero, not as a prerequisite step.

## 1. What Qwen-Image-Layered actually is

- End-to-end diffusion model (RGBA-VAE + variable-layer VLD-MMDiT) that decomposes a single RGB image into multiple **semantically disentangled RGBA layers**; each layer independently editable; recursive decomposition supported. Trained on layers extracted from real PSD files. Source: [arXiv 2512.15603](https://arxiv.org/abs/2512.15603), [GitHub QwenLM/Qwen-Image-Layered](https://github.com/QwenLM/Qwen-Image-Layered), [HF model card](https://huggingface.co/Qwen/Qwen-Image-Layered).
- Output is **true separated layers with alpha** (RGBA PNGs, bottom-to-top order), not edited flat images. Occluded regions are hallucinated by the model. Cross-checked: HF card + fal docs + a hands-on third-party tutorial ([DataCamp](https://www.datacamp.com/tutorial/qwen-image-layered)).
- Inputs: image (+ optional whole-image caption), layer count, resolution (640 or 1024 recommended), steps/CFG. The prompt **cannot** control what goes in which layer ([GitHub](https://github.com/QwenLM/Qwen-Image-Layered), [ComfyUI docs](https://docs.comfy.org/tutorials/image/qwen/qwen-image-layered)).
- Self-hosting is impractical: ~57 GB of weights, high-VRAM GPU ([DataCamp](https://www.datacamp.com/tutorial/qwen-image-layered)).

## 2. Provider comparison (US dev, credit card, today)

| Provider | Endpoint | Available? | Price | I/O | Friction |
|---|---|---|---|---|---|
| **fal.ai** | `fal-ai/qwen-image-layered` | Yes | **$0.05/decomposition**, flat regardless of layers ([model page](https://fal.ai/models/fal-ai/qwen-image-layered), [dev guide](https://fal.ai/learn/devs/qwen-image-layered-image-to-image-developer-guide)) | `image_url`, `num_layers` 1–10 (def 4) → array of RGBA PNG URLs; 15–30 s | Low: email/GitHub + card |
| **Replicate** | `qwen/qwen-image-layered` | Yes ([model page](https://replicate.com/qwen/qwen-image-layered)) | ~$0.03/run per [DataCamp hands-on](https://www.datacamp.com/tutorial/qwen-image-layered); exact current price UNVERIFIED (page renders pricing client-side) | `image`, `num_layers` 2–8, `go_fast`, `seed` → list of RGBA PNGs, index 0 = background | Low: GitHub + card |
| **WaveSpeedAI** | `wavespeed-ai/qwen-image/layered` | Yes | $0.025 × num_layers ⇒ $0.10 for 4 layers ([model page](https://wavespeed.ai/models/wavespeed-ai/qwen-image/layered)) — pricier than fal at typical counts | image/URL + `num_layers` → RGBA layer URLs | Low, but lesser-known provider |
| **DashScope / Alibaba Model Studio** | — | **No.** `qwen-image-layered` absent from the model catalog in both international (Singapore) and China listings; intl image lineup is qwen text-to-image, Wan, Z-Image only ([Model Studio models](https://www.alibabacloud.com/help/en/model-studio/models)) | — | — | — |
| **OpenRouter** | — | **No.** No Qwen image-generation models; image output limited to other families ([OpenRouter image-gen docs](https://openrouter.ai/docs/guides/overview/multimodal/image-generation)) | — | — | — |

Free option for manual spot-checks: the official [Hugging Face Space demo](https://huggingface.co/Qwen/Qwen-Image-Layered) (upload, download layers by hand).

## 3. Fit for the mockup→assets use case (caveats)

- **Resolution ceiling**: model operates at 640/1024 px ([GitHub](https://github.com/QwenLM/Qwen-Image-Layered), [ComfyUI](https://docs.comfy.org/tutorials/image/qwen/qwen-image-layered)). A tall full-page mockup gets downscaled; small nav/body text in layers will degrade.
- **Generative, not surgical**: layers are re-synthesized, so logo glyphs and UI text may shift subtly; reconstructed backgrounds keep "faint shadow remnants" ([DataCamp](https://www.datacamp.com/tutorial/qwen-image-layered)); splits don't always match what you want — depends on image complexity ([community report, Threads](https://www.threads.com/@cooljerrett/post/DSfDHBeia40/)).
- Slow per image (50-step baseline; "this model is slow" — [ComfyUI](https://docs.comfy.org/tutorials/image/qwen/qwen-image-layered)). Fine for one-off lead-gen mockups.

## 4. Fallback assessment

The coding agent rebuilds **text, layout, and backgrounds as HTML/CSS anyway** — those should never be image assets. The only artifacts worth extracting from a mockup are the **logo and hero/illustration imagery**. So:

- **Flat mockup → image-to-code** (workspace already has the `image-to-code` skill + coding agents): fully legitimate primary path; a VLM reads layout/text/colors from the flat PNG without any decomposition. **Decomposition adds zero value for layout/text fidelity.**
- Where decomposition *does* add value: clean alpha-cut logo/hero when text or UI elements overlap them — inpainted layers beat cropping. That value is narrow.
- Cheaper targeted alternatives for that narrow job: BiRefNet on fal ([fal-ai/birefnet/v2](https://fal.ai/models/fal-ai/birefnet/v2), billed per compute-second — effectively sub-cent) or Bria RMBG 2.0 at $0.018/image ([fal](https://fal.ai/models/fal-ai/bria/background/remove)) on cropped regions.
- Often best of all: **regenerate** the hero/logo fresh (gpt-image-2 transparent-background output) at full resolution instead of extracting a ≤1024px diffusion reconstruction of an already-AI-generated asset.

## 5. Recommendation

**Do the flat-mockup → image-to-code proof first** — it is the load-bearing step and costs nothing new. In parallel, **buy ~$5 of fal.ai credit** and run 3–5 representative mockups through `fal-ai/qwen-image-layered` (num_layers 4–6) purely to judge whether the extracted logo/hero layers are crisp enough at ≤1024px to drop into the rebuilt site. Decision rule: if layers come back mushy or mis-split (likely on tall, text-dense pages), drop decomposition permanently and regenerate assets instead; do not build the pipeline around it. Avoid DashScope (model not served) and WaveSpeed (2× fal's price at 4 layers).

### Verification status
- Capability, output format, fal endpoint+price, Replicate endpoint, DashScope absence, OpenRouter absence: verified against ≥2 independent sources each (provider docs + GitHub/HF/arXiv + hands-on tutorial).
- UNVERIFIED: exact current Replicate per-run price (~$0.03 from DataCamp only); fal "$0.05 per image" wording could be read per-output-layer, but fal's own dev guide states cost does not vary by layer count; exact BiRefNet per-image cost on fal (compute-second billing).
