# DEV-028 Source-Lock License Inventory

**Status:** Source/lock attribution inventory only. The exact frozen Python payload and emitted frontend bundle do not exist yet; this document does not assert final payload membership or complete redistribution notices. See `THIRD-PARTY-NOTICES` and `docs/opensource/license-audit.md`.

## Python — `requirements-lock.txt`

114 pinned distributions. Rows are the exact source lock versions; each license label comes from exact-version package metadata. The final payload audit must confirm which distributions ship and retain their actual bundled license/copyright files.

| Distribution | Locked version | Declared license | Metadata basis |
|---|---:|---|---|
| `aiohappyeyeballs` | `2.7.1` | PSF-2.0 | [exact-version release metadata](https://pypi.org/project/aiohappyeyeballs/2.7.1/) |
| `aiohttp` | `3.14.3` | Apache-2.0 AND MIT | exact-version installed metadata (version matched lock) |
| `aiosignal` | `1.4.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `aiosqlite` | `0.22.1` | MIT | exact-version installed metadata (version matched lock) |
| `annotated-doc` | `0.0.5` | MIT | exact-version installed metadata (version matched lock) |
| `annotated-types` | `0.8.0` | MIT | exact-version installed metadata (version matched lock) |
| `anyio` | `4.15.1` | MIT | exact-version installed metadata (version matched lock) |
| `async-timeout` | `5.0.1` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/async-timeout/5.0.1/) |
| `attrs` | `26.1.0` | MIT | exact-version installed metadata (version matched lock) |
| `backoff` | `2.2.1` | MIT | [exact-version release metadata](https://pypi.org/project/backoff/2.2.1/) |
| `bcrypt` | `5.0.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `build` | `1.6.1` | MIT | exact-version installed metadata (version matched lock) |
| `certifi` | `2026.7.22` | MPL-2.0 | exact-version installed metadata (version matched lock) |
| `charset-normalizer` | `3.5.1` | MIT | exact-version installed metadata (version matched lock) |
| `chromadb` | `1.0.20` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/chromadb/1.0.20/) |
| `click` | `8.5.0` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `colorama` | `0.4.6` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `coloredlogs` | `15.0.1` | MIT | [exact-version release metadata](https://pypi.org/project/coloredlogs/15.0.1/) |
| `distro` | `1.9.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `durationpy` | `0.11` | MIT | exact-version installed metadata (version matched lock) |
| `fastapi` | `0.141.1` | MIT | exact-version installed metadata (version matched lock) |
| `filelock` | `4.0.8` | MIT | [exact-version release metadata](https://pypi.org/project/filelock/4.0.8/) |
| `flatbuffers` | `25.12.19` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `frozenlist` | `1.8.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `fsspec` | `2026.9.0` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `googleapis-common-protos` | `1.75.5` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/googleapis-common-protos/1.75.5/) |
| `grpcio` | `1.84.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `h11` | `0.16.0` | MIT | exact-version installed metadata (version matched lock) |
| `httpcore` | `1.0.9` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `httpcore2` | `2.13.1` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `httpx` | `0.28.1` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `httpx2` | `2.13.1` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `huggingface-hub` | `0.36.2` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/huggingface-hub/0.36.2/) |
| `humanfriendly` | `10.0` | MIT | [exact-version release metadata](https://pypi.org/project/humanfriendly/10.0/) |
| `idna` | `3.20` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `importlib-resources` | `7.1.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `Jinja2` | `3.1.6` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `jiter` | `0.17.0` | MIT | exact-version installed metadata (version matched lock) |
| `jsonpatch` | `1.33` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `jsonpointer` | `3.1.1` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `jsonschema` | `4.26.0` | MIT | exact-version installed metadata (version matched lock) |
| `jsonschema-specifications` | `2025.9.1` | MIT | exact-version installed metadata (version matched lock) |
| `kubernetes` | `36.0.3` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `langchain-core` | `1.6.5` | MIT | [exact-version release metadata](https://pypi.org/project/langchain-core/1.6.5/) |
| `langchain-protocol` | `0.0.19` | MIT | exact-version installed metadata (version matched lock) |
| `langgraph` | `1.2.12` | MIT | exact-version installed metadata (version matched lock) |
| `langgraph-checkpoint` | `4.2.0` | MIT | exact-version installed metadata (version matched lock) |
| `langgraph-checkpoint-sqlite` | `3.1.1` | MIT | exact-version installed metadata (version matched lock) |
| `langgraph-prebuilt` | `1.1.0` | MIT | exact-version installed metadata (version matched lock) |
| `langgraph-sdk` | `0.4.5` | MIT | exact-version installed metadata (version matched lock) |
| `langsmith` | `0.14.1` | MIT | [exact-version release metadata](https://pypi.org/project/langsmith/0.14.1/) |
| `markdown-it-py` | `4.2.0` | MIT | exact-version installed metadata (version matched lock) |
| `MarkupSafe` | `3.0.3` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `mdurl` | `0.1.2` | MIT | exact-version installed metadata (version matched lock) |
| `mmh3` | `5.3.1` | MIT | [exact-version release metadata](https://pypi.org/project/mmh3/5.3.1/) |
| `mpmath` | `1.3.0` | BSD-3-Clause | [exact-version release metadata](https://pypi.org/project/mpmath/1.3.0/) |
| `multidict` | `6.9.1` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `numpy` | `2.2.6` | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [exact-version release metadata](https://pypi.org/project/numpy/2.2.6/) |
| `oauthlib` | `4.0.0` | BSD-3-Clause | [exact-version release metadata](https://pypi.org/project/oauthlib/4.0.0/) |
| `onnxruntime` | `1.23.2` | MIT | [exact-version release metadata](https://pypi.org/project/onnxruntime/1.23.2/) |
| `openai` | `3.19.2` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `opentelemetry-api` | `1.45.0` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/opentelemetry-api/1.45.0/) |
| `opentelemetry-exporter-otlp-common` | `0.66b0` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/opentelemetry-exporter-otlp-common/0.66b0/) |
| `opentelemetry-exporter-otlp-proto-common` | `1.45.0` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/opentelemetry-exporter-otlp-proto-common/1.45.0/) |
| `opentelemetry-exporter-otlp-proto-grpc` | `1.45.0` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/opentelemetry-exporter-otlp-proto-grpc/1.45.0/) |
| `opentelemetry-proto` | `1.45.0` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/opentelemetry-proto/1.45.0/) |
| `opentelemetry-sdk` | `1.45.0` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/opentelemetry-sdk/1.45.0/) |
| `opentelemetry-semantic-conventions` | `0.66b0` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/opentelemetry-semantic-conventions/0.66b0/) |
| `orjson` | `3.12.0` | MPL-2.0 AND (Apache-2.0 OR MIT) | exact-version installed metadata (version matched lock) |
| `ormsgpack` | `1.12.2` | Apache-2.0 OR MIT | exact-version installed metadata (version matched lock) |
| `overrides` | `7.7.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `packaging` | `26.3` | Apache-2.0 OR BSD-2-Clause | exact-version installed metadata (version matched lock) |
| `posthog` | `5.4.0` | MIT | [exact-version release metadata](https://pypi.org/project/posthog/5.4.0/) |
| `propcache` | `0.5.4` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `protobuf` | `7.36.2` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `pybase64` | `1.5.0` | BSD-2-Clause | exact-version installed metadata (version matched lock) |
| `pydantic` | `2.13.5` | MIT | exact-version installed metadata (version matched lock) |
| `pydantic_core` | `2.46.5` | MIT | exact-version installed metadata (version matched lock) |
| `pygments` | `2.21.0` | BSD-2-Clause | exact-version installed metadata (version matched lock) |
| `pypika` | `0.51.1` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `pyproject-hooks` | `1.3.3` | MIT | exact-version installed metadata (version matched lock) |
| `pyreadline3` | `3.5.6` | BSD (SPDX variant unspecified) | [exact-version release metadata](https://pypi.org/project/pyreadline3/3.5.6/) |
| `python-dateutil` | `2.9.0.post0` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `python-dotenv` | `1.2.3` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `python-multipart` | `0.0.32` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `PyYAML` | `6.0.3` | MIT | exact-version installed metadata (version matched lock) |
| `referencing` | `0.37.0` | MIT | exact-version installed metadata (version matched lock) |
| `requests` | `2.34.2` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `requests-oauthlib` | `2.0.0` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `requests-toolbelt` | `1.0.0` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `rich` | `15.0.0` | MIT | exact-version installed metadata (version matched lock) |
| `rpds-py` | `0.30.0` | MIT | [exact-version release metadata](https://pypi.org/project/rpds-py/0.30.0/) |
| `shellingham` | `1.5.4` | ISC License | exact-version installed metadata (version matched lock) |
| `six` | `1.17.0` | MIT | exact-version installed metadata (version matched lock) |
| `sniffio` | `1.3.1` | MIT | exact-version installed metadata (version matched lock) |
| `sqlite-vec` | `0.1.9` | MIT OR Apache-2.0 | exact-version installed metadata (version matched lock) |
| `starlette` | `1.7.0` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `sympy` | `1.14.0` | BSD-3-Clause | [exact-version release metadata](https://pypi.org/project/sympy/1.14.0/) |
| `tenacity` | `9.1.4` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `tokenizers` | `0.22.2` | Apache-2.0 | [exact-version release metadata](https://pypi.org/project/tokenizers/0.22.2/) |
| `tomli` | `2.4.1` | MIT | [exact-version release metadata](https://pypi.org/project/tomli/2.4.1/) |
| `tqdm` | `4.70.1` | MPL-2.0 AND MIT | exact-version installed metadata (version matched lock) |
| `truststore` | `0.10.4` | MIT | exact-version installed metadata (version matched lock) |
| `typer` | `0.27.2` | MIT | exact-version installed metadata (version matched lock) |
| `typing_extensions` | `4.16.0` | PSF-2.0 | exact-version installed metadata (version matched lock) |
| `typing-inspection` | `0.4.4` | MIT | exact-version installed metadata (version matched lock) |
| `urllib3` | `2.8.0` | MIT | exact-version installed metadata (version matched lock) |
| `uuid_utils` | `0.17.1` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `uvicorn` | `0.54.0` | BSD-3-Clause | [exact-version release metadata](https://pypi.org/project/uvicorn/0.54.0/) |
| `websocket-client` | `1.9.2` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `websockets` | `16.1.1` | BSD-3-Clause | exact-version installed metadata (version matched lock) |
| `xxhash` | `4.0.1` | BSD-2-Clause | exact-version installed metadata (version matched lock) |
| `yarl` | `1.25.1` | Apache-2.0 | exact-version installed metadata (version matched lock) |
| `zstandard` | `0.25.0` | BSD-3-Clause | exact-version installed metadata (version matched lock) |

## Frontend — `frontend/package-lock.json`

11 production lock entries. Build-only tooling is excluded by the lock classification; emitted bundle membership is still pending.

| Package | Locked version | Declared license |
|---|---:|---|
| `@remix-run/router` | `1.23.4` | MIT |
| `@types/trusted-types` | `2.0.7` | MIT |
| `dompurify` | `3.4.15` | (MPL-2.0 OR Apache-2.0) |
| `js-tokens` | `4.0.0` | MIT |
| `loose-envify` | `1.4.0` | MIT |
| `marked` | `12.0.2` | MIT |
| `react` | `18.3.1` | MIT |
| `react-dom` | `18.3.1` | MIT |
| `react-router` | `6.30.6` | MIT |
| `react-router-dom` | `6.30.6` | MIT |
| `scheduler` | `0.23.2` | MIT |

## Other selected components

| Component | Identity | License / evidence |
|---|---|---|
| Project | Open Marketing OS | Apache-2.0; see `LICENSE` and `NOTICE`. |
| htmx | 2.0.4 (`app/static/htmx.min.js`) | 0BSD; exact upstream notice at `LICENSES/htmx-0BSD.txt`; source: https://github.com/bigskysoftware/htmx/blob/v2.0.4/LICENSE |
| llama.cpp | b11429 / d81235049384534c167caea52b85a694f6103d14 | MIT; archive SHA-256 `1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe`. Preserve runtime `LICENSE` and `LICENSE-LLVM-OpenMP` notices in Local payload. |
| Local model | Qwen2.5-7B-Instruct Q4_K_M — Qwen/Qwen2.5-7B-Instruct-GGUF @ `293ca9a10157b0e5fc5cb32af8b636a88bede891` | Catalog declares Apache-2.0; weights are not in source candidate/installer. This is a source catalog disclosure, not a weight redistribution grant. |
| Vendored Marketing Skills | coreyhaines31/marketingskills v2.11.1 / `5b2c0007766c6a1cf1d53fd8fc73e979e0821022` | MIT; retain `.agents/LICENSE`. |

## Pending payload evidence

- Exact Python payload package set and retained license files must be checked after the final payload build.
- Exact emitted frontend bundle contents and retained package copyright/license texts must be checked after the final payload build.
- pyreadline3==3.5.6 metadata declares BSD generically; retain its exact locked wheel license file and determine SPDX variant from that file in payload QA.

The installed Inno Setup compiler showed “Non-commercial use only” in DEV-029. The vendor commercial licensing page requests commercial users to purchase a commercial license; its FAQ says purchase is not strictly required and may wait until installers are production-ready. The current decision remains **PRE-PRODUCTION / NEEDS-FOUNDER-CONFIRMATION**; no legal conclusion is made. Sources: https://jrsoftware.org/isorder.php and https://jrsoftware.org/isinfo.php.
