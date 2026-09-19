# Development consensus: segment materials and separate prompt expansion

Approved in the current conversation on 2026-09-19. Implementation is authorized.

- Start from merged 2c1361d; work on feature/segment-materials-expander. Do not publish to main during development.
- Keep the first five H3LVUnified outputs compatible. Add one current-segment material packet plus nine image outputs. Controller removes unused H3 image inputs per submission; do not feed black placeholders.
- Project default references and material note; per-segment explicit inheritance/custom mode. Custom empty is an error, never an implicit fallback. Preserve older canvas-reference workflows until explicitly migrated.
- Every segment chooses performance or environment. Environment prompts must not require a singer or lip articulation. Original audio remains in final assembly.
- Separate H3LVPromptExpand node in same package: OpenAI-compatible provider, text or multi-image expansion, editable rule, reviewable output and cache. Default test provider OFOX, model qwen/qwen3.8-flash; actual two-image probe already passed. No secrets in workflows, project files or logs.
- Configuration stored under ComfyUI user/H3LongVideo. No dependency/model installation implied. User authorized paid provider tests and local plugin replacement; use bounded requests and no automatic retries.
- Preserve approved-project fingerprints for untouched legacy projects. Changed effective references/note/type invalidate affected takes and approval. Retain prior generated files.
- Use the user-requested selflift dual-sampling test workflow copy. Do not change sampling/model settings or original workflow.
- Acceptance: default inheritance/custom override, differing counts/order, empty custom rejection, missing images, cache invalidation, provider failures, old workflows, speaking/performance/environment, and real local runtime tests. Report image-generation acceptance separately from static/provider tests.
- Preview HTML remains throwaway; no unreviewed prototype decisions are production guarantees.
