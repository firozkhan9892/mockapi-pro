"""Tests for the image-provider architecture.

These do NOT require a live Flux server or any model download:
- FluxProvider with no env configured -> graceful "not configured" failure.
- get_provider() dispatches by model and falls back to PlaceholderProvider.
- PlaceholderProvider returns deterministic, valid PNG bytes.
- FluxProvider HTTP behaviour is validated with a stubbed requests session.
"""

import base64
import json
import struct
import sys
import zlib
from unittest import mock

sys.path.insert(0, ".")

from images import utils
from images.providers import (
    FluxProvider,
    GenerationStatus,
    PlaceholderProvider,
    ProviderConfig,
    get_provider,
)
import images.providers.flux as flux_mod

p = f = 0
def T(n, c):
    global p, f
    if c:
        print(f"  PASS  {n}"); p += 1
    else:
        print(f"  FAIL  {n}"); f += 1

print("=== PROVIDER TESTS ===\n")

# [1] Configuration / graceful failure
print("[1] Configuration & graceful failure")
provider = FluxProvider(ProviderConfig.from_env("FLUX"))
T("FluxProvider not configured by default", not provider.configured)
result = provider.generate("a cat", "", 512, 512, "flux-schnell")
T("Unconfigured provider returns failed", result.status == GenerationStatus.FAILED)
T("Unconfigured error mentions config", "configured" in (result.error or ""))

# [2] Registry dispatch & fallback
print("\n[2] Registry dispatch")
pb = get_provider("flux-schnell")
T("flux-schnell falls back to Placeholder when unconfigured",
  isinstance(pb, PlaceholderProvider))
T("PlaceholderProvider configured flag", pb.configured is True)
other = get_provider("stable-diffusion-xl")
T("unknown model falls back to Placeholder", isinstance(other, PlaceholderProvider))

# [3] Placeholder provider
print("\n[3] Placeholder provider")
res = PlaceholderProvider().generate("hello", "bad", 64, 48, "flux-schnell")
T("Placeholder status completed", res.status == GenerationStatus.COMPLETED)
T("Placeholder returns PNG", res.image_bytes[:8] == b"\x89PNG\r\n\x1a\n")
T("Different prompts differ",
  PlaceholderProvider().generate("a", "", 32, 32, "m").image_bytes !=
  PlaceholderProvider().generate("b", "", 32, 32, "m").image_bytes)

# [4] FluxProvider HTTP - synchronous base64
print("\n[4] FluxProvider HTTP")
cfg = ProviderConfig(api_url="https://flux.example.com", api_key="secret",
                     timeout=5, poll_interval=0.01)
fp = FluxProvider(cfg)
T("FluxProvider configured when url set", fp.configured)

png = utils.make_placeholder_png(8, 8, "x")
b64 = base64.b64encode(png).decode()

# Case A: synchronous 200 with base64 image field
fake_resp = mock.Mock()
fake_resp.status_code = 200
fake_resp.headers = {"Content-Type": "application/json"}
fake_resp.json.return_value = {"image": b64}
fake_resp.text = json.dumps(fake_resp.json.return_value)
with mock.patch.object(flux_mod.requests, "post", return_value=fake_resp) as mock_post:
    res = fp.generate("a dog", "", 8, 8, "flux-schnell")
T("Sync base64 -> completed", res.status == GenerationStatus.COMPLETED)
T("Sync image bytes match", res.image_bytes == png)
T("Auth header sent", mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret")

# Case B: synchronous raw PNG bytes
fake_resp2 = mock.Mock()
fake_resp2.status_code = 200
fake_resp2.headers = {"Content-Type": "image/png"}
fake_resp2.content = png
with mock.patch.object(flux_mod.requests, "post", return_value=fake_resp2):
    res = fp.generate("a dog", "", 8, 8, "flux-schnell")
T("Sync raw bytes -> completed", res.status == GenerationStatus.COMPLETED and res.image_bytes == png)

# Case C: 202 async -> poll -> fetch image
fake_202 = mock.Mock()
fake_202.status_code = 202
fake_202.headers = {"Content-Type": "application/json"}
fake_202.json.return_value = {"id": "job-123"}
fake_202.text = json.dumps({"id": "job-123"})

poll_resp = mock.Mock()
poll_resp.status_code = 200
poll_resp.headers = {"Content-Type": "application/json"}
poll_resp.json.return_value = {"status": "completed", "id": "job-123"}
poll_resp.text = json.dumps({"status": "completed"})

image_resp = mock.Mock()
image_resp.status_code = 200
image_resp.headers = {"Content-Type": "image/png"}
image_resp.content = png

with mock.patch.object(flux_mod.requests, "post", return_value=fake_202) as post_mock, \
     mock.patch.object(flux_mod.requests, "get", side_effect=[poll_resp, image_resp]) as get_mock:
    res = fp.generate("a bird", "", 8, 8, "flux-schnell")
T("Async poll -> completed", res.status == GenerationStatus.COMPLETED and res.image_bytes == png)
T("Async fetched image endpoint", any("/v1/image/job-123" in str(c) for c in get_mock.call_args_list))
T("Async did not re-post generate", post_mock.call_count == 1)

# Case D: async failure status
poll_fail = mock.Mock()
poll_fail.status_code = 200
poll_fail.headers = {"Content-Type": "application/json"}
poll_fail.json.return_value = {"status": "failed", "error": "GPU OOM"}
poll_fail.text = json.dumps({"status": "failed"})

with mock.patch.object(flux_mod.requests, "post", return_value=fake_202), \
     mock.patch.object(flux_mod.requests, "get", return_value=poll_fail):
    res = fp.generate("a dog", "", 8, 8, "flux-schnell")
T("Async failure -> failed", res.status == GenerationStatus.FAILED)

# Case E: sync 200 json without image and no id -> failed
fake_oops = mock.Mock()
fake_oops.status_code = 200
fake_oops.headers = {"Content-Type": "application/json"}
fake_oops.json.return_value = {"foo": "bar"}
fake_oops.text = json.dumps({"foo": "bar"})
with mock.patch.object(flux_mod.requests, "post", return_value=fake_oops):
    res = fp.generate("a dog", "", 8, 8, "flux-schnell")
T("JSON without image -> failed", res.status == GenerationStatus.FAILED)

# Case F: HTTP error
fake_err = mock.Mock()
fake_err.status_code = 503
fake_err.headers = {"Content-Type": "text/plain"}
fake_err.text = "maint"
with mock.patch.object(flux_mod.requests, "post", return_value=fake_err):
    res = fp.generate("a dog", "", 8, 8, "flux-schnell")
T("Upstream HTTP error -> failed", res.status == GenerationStatus.FAILED and "503" in (res.error or ""))

# Case G: connection exception -> failed
with mock.patch.object(flux_mod.requests, "post", side_effect=Exception("network")):
    res = fp.generate("a dog", "", 8, 8, "flux-schnell")
T("Request exception -> failed", res.status == GenerationStatus.FAILED and "network" in (res.error or ""))

# Case H: data-uri base64
fake_data = mock.Mock()
fake_data.status_code = 200
fake_data.headers = {"Content-Type": "application/json"}
fake_data.json.return_value = {"image": "data:image/png;base64," + b64}
fake_data.text = "{}"
with mock.patch.object(flux_mod.requests, "post", return_value=fake_data):
    res = fp.generate("a dog", "", 8, 8, "flux-schnell")
T("data-uri base64 decodes", res.status == GenerationStatus.COMPLETED and res.image_bytes == png)

print(f"\n=== {p} passed, {f} failed ===")
sys.exit(1 if f else 0)
