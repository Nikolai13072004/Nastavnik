"""RAG evaluation harness for Vedomo.

Drives the *real* retrieval + answer pipeline (``knowledge_base`` +
``app_services.chat_service``) over a small dataset of questions and scores the
results, so answer quality can be measured and tracked as the pipeline,
prompts or retrieval params change.

Layout:

* ``evals.dataset``  - dataset schema + loader (pure, no heavy deps).
* ``evals.scoring``  - scoring functions (pure, unit-tested in CI).
* ``evals.runner``   - orchestration: ingest -> retrieve -> answer -> score.
                       Imports the heavy backend lazily.
* ``evals.run_eval`` - CLI entrypoint (``python -m evals.run_eval``).

The runner needs the embedding model + a configured LLM, so it is a local /
dev-box tool, not part of CI. CI only exercises the pure scoring + parsing.
"""
