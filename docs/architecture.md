# Architecture

The API is the only public backend. Authentication, authorization, session limits, and
project membership are enforced before calls reach adapters.

```text
Web / Flutter -> HTTPS/WSS API -> Voice/Agent router -> STT, RAG, LLM, TTS
                                -> read-only monitoring tools
                                -> persistent identity and audit store
```

`jarvis.adapters` defines replaceable STT, LLM, and TTS protocols. The included fake
implementations make tests deterministic. Real engine adapters must preserve cancellation,
timeouts, bounded buffers, explicit media types, and error mapping.

RAG access is derived from token identity plus database membership, never from prompt text
or untrusted tenant IDs. Retrieval filters always use server-derived tenant/project IDs.
Files must resolve inside configured roots; sensitive and generated files are ignored.

Monitoring exposes named functions with validated project IDs and fixed time windows.
The LLM never receives raw PromQL capability or infrastructure write access.

