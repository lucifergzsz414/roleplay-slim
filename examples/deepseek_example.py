"""Zero-code-change integration: swap your app's base_url to the proxy
and every request gets compressed automatically.

Quick start:
  1. export UPSTREAM_API_KEY=sk-your-real-key
  2. roleplay-slim-proxy --config examples/example_config.toml
  3. Point your app at http://127.0.0.1:8791/v1 instead of the
     real provider URL.

With the OpenAI Python client:
  from openai import OpenAI
  client = OpenAI(
      base_url="http://127.0.0.1:8791/v1",
      api_key="not-used-by-the-proxy",  # proxy uses its own UPSTREAM_API_KEY
  )
  response = client.chat.completions.create(
      model="deepseek-chat",
      messages=[...],
  )
  # The proxy compresses messages before forwarding; your app doesn't
  # need to know roleplay-slim exists.
"""
print("See the comment block above for usage instructions.")
