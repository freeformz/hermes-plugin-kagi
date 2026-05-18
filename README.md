# hermes-plugin-kagi

A [Kagi Search](https://kagi.com/) backend plugin for [hermes-agent](https://github.com/NousResearch/hermes-agent). Adds `kagi` as a selectable provider for hermes's built-in `web_search` and `web_extract` tools.

Pure Python — uses the official [`kagi-openapi-python`](https://github.com/kagisearch/kagi-openapi-python) client. Does not require the kagi-mcp binary.

## Capabilities

| Tool | Backed by |
|------|-----------|
| `web_search` | `POST https://kagi.com/api/v1/search` (workflow=`search`) |
| `web_extract` | `POST https://kagi.com/api/v1/extract` (chunked 10-URLs-per-call) |

## Install

```sh
hermes plugins install https://github.com/freeformz/hermes-plugin-kagi
pip install git+https://github.com/kagisearch/kagi-openapi-python.git
```

The first command clones this repo into `~/.hermes/plugins/web-kagi/`. The second installs the Kagi Python client (it's not on PyPI yet, so it can't ship as a normal pip dep).

## Configure

Set your API key (create one at https://kagi.com/settings/api_keys):

```sh
echo 'KAGI_API_KEY=your-kagi-api-key' >> ~/.hermes/.env
```

Then in `~/.hermes/config.yaml`, enable the plugin and route web traffic through it:

```yaml
plugins:
  enabled:
    - web-kagi

web:
  search_backend: "kagi"
  extract_backend: "kagi"
  # OR shared:
  # backend: "kagi"
```

Verify hermes sees it:

```sh
hermes tools
```

`Kagi` should appear in the Web Search / Web Extract picker, badged `paid`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `KAGI_API_KEY` | Yes | Kagi API key from [kagi.com/settings/api_keys](https://kagi.com/settings/api_keys) |
| `KAGI_API_BASE_URL` | No | Override the API host (default `https://kagi.com/api/v1`) |

## Response Mapping

### `search`

Kagi's `data.search[]` array (the primary web results — `infobox`, `adjacent_question`, `news`, etc. are dropped from the `web` result list to keep the hermes contract clean) is mapped to:

```python
{
  "success": True,
  "data": {
    "web": [
      {"title": ..., "url": ..., "description": <snippet>, "position": <1-based>},
      ...
    ]
  }
}
```

### `extract`

Kagi returns markdown for each page in `data[].markdown`; the plugin emits per-URL documents using that as both `content` and `raw_content`. Kagi's `errors[]` entries become documents with an `error` field. URL lists longer than 10 are chunked across multiple API calls (Kagi caps per-call at 10).

## Why is it Kagi-specific?

Kagi is not a built-in hermes provider, and its API is paid-only with per-call billing — different from the free/freemium providers bundled with hermes. Living as a separate plugin keeps the upstream repo lean and lets this plugin track Kagi's v1 (preview) API changes independently.

## License

MIT
