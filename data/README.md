# data

    data/raw/<set>/<doc>/
      <doc>.pdf            the source PDF
      content_list.json    MinerU output for that PDF

`<set>` groups documents (e.g. `books`, `papers`); `<doc>` is a slug that
becomes the default `doc_id`. Nothing under `data/raw/` is committed (see
`.gitignore`). Record the licence of every document in a `LICENCE.txt` next to
it before adding it locally; do not add documents you are not allowed to
redistribute to any shared bucket.

Built layouts go to `evals/cases/<doc>/layout.json` (also ignored) or wherever
`-o` points.
