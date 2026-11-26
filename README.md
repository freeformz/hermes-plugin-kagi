# hermes-plugin-kagi

A [Kagi Search](https://kagi.com/) backend plugin for [hermes-agent](https://github.com/NousResearch/hermes-agent). Adds `kagi` as a selectable provider for hermes's built-in `web_search` and `web_extract` tools.

Pure Python, no dependencies of its own: it calls the [Kagi v1 API](https://kagi.com/api/docs/openapi) with `httpx`, which Hermes already ships. (The generated [`kagi-client`](https://github.com/kagisearch/kagi-openapi-python) package lags the live API, so it isn't used.)

## Capabilities

| Tool | Backed by |
|------|-----------|
| `web_search` | `POST https://kagi.com/api/v1/search` |
| `web_extract` | `POST https://kagi.com/api/v1/extract` (10 URLs per call) |

## Requirements

Hermes Agent 0.23.4 or newer (the version it's tested on; declared as `requires_hermes` in `plugin.yaml`).

## Install

```sh
hermes plugins install https://github.com/freeformz/hermes-plugin-kagi
```

This clones the repo into `~/.hermes/plugins/web-kagi/` and prompts for `KAGI_API_KEY` if it isn't set.

## Configure

Set your API key (create one at https://kagi.com/api/keys):

```sh
echo 'KAGI_API_KEY=your-kagi-api-key' >> ~/.hermes/.env
```

Then in `~/.hermes/config.yaml` (or a profile's `config.yaml`), enable the plugin and route web traffic through it:

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

## Profiles

Credentials are read with Hermes' profile-scoped `agent.secret_scope.get_secret`, so in a multiplexed gateway each profile uses the `KAGI_API_KEY` from its own `.env`. A profile without a key gets an error instead of silently using another profile's key. Each profile also needs the plugin enabled in its own `plugins.enabled` and its own `web.*_backend` settings.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `KAGI_API_KEY` | Yes | Kagi API key from [kagi.com/api/keys](https://kagi.com/api/keys) |
| `KAGI_API_BASE_URL` | No | Override the API host (default `https://kagi.com/api/v1`) |

## Response Mapping

### `search`

Kagi's `data.search[]` array (the primary web results) is mapped to the hermes contract. The other result groups Kagi returns (`infobox`, `news`, `video`, `related_search`, `direct_answer`, ...) are dropped, because hermes's `web_search` result has a single list of web hits. Titles and snippets come from Kagi with HTML entities and `<strong>` highlights; the plugin turns them into plain text.

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

Kagi returns markdown for each page in `data[].markdown`; the plugin emits one document per requested URL, in order, with that markdown as both `content` and `raw_content`. A page Kagi couldn't fetch (`data[].error`, or no markdown) becomes a document with an `error` field.

URL lists longer than 10 are split across calls. Kagi rejects a whole call (HTTP 400) when any URL in it is invalid, naming it as `pages[i]`; the plugin reports those URLs as errors and retries the rest once.

### Errors

API failures (bad key, IP not allowed, rate limit or usage exhausted) come back as `{"success": False, "error": ...}` with Kagi's own message, the HTTP status and Kagi's trace ID, which Kagi support asks for.

## What it does on your machine

- Sends your search queries and the URLs you ask it to extract to `kagi.com` (or `KAGI_API_BASE_URL`), with your `KAGI_API_KEY`. Kagi bills each call to your account.
- Nothing else: no other network hosts, no shell commands, no files written, no background processes, no telemetry, no self-updater. It registers one web provider and no tools or hooks.

## Why is it Kagi-specific?

Kagi is not a built-in hermes provider, and its API is paid-only with per-call billing — different from the free/freemium providers bundled with hermes. Living as a separate plugin keeps the upstream repo lean and lets this plugin track Kagi's v1 API changes independently.

## License

MIT
