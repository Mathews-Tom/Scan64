# Maia opponent setup

Scan64 does not bundle or download Maia weights. Obtain Lc0 and any model weights through an operator-controlled process, then keep the weights outside the repository and release artifacts. Review the applicable model licence and redistribution terms before any distribution decision.

Create an operator-owned configuration file, for example `/etc/scan64/maia.toml`:

```toml
[maia]
binary_path = "/opt/homebrew/bin/lc0"
threads = 1

[maia.checkpoints]
1100 = "/srv/scan64/maia/maia-1100.pb.gz"
1500 = "/srv/scan64/maia/maia-1500.pb.gz"
1900 = "/srv/scan64/maia/maia-1900.pb.gz"
```

Set `SCAN64_MAIA_CONFIG` to the absolute path before starting the API. Select it for a play session with `opponent_config.provider = "maia"` and a numeric `opponent_config.strength` rating.

Maia selection fails closed when `SCAN64_MAIA_CONFIG`, the Lc0 binary, or the selected weights file is missing. Use Stockfish explicitly with `opponent_config.provider = "stockfish"` for conventional engine play.

Run the real-model quality gate only with locally provisioned binaries and weights:

```text
SCAN64_MAIA_BINARY=/opt/homebrew/bin/lc0 SCAN64_MAIA_1500_WEIGHTS=/srv/scan64/maia/maia-1500.pb.gz SCAN64_STOCKFISH_BINARY=/opt/homebrew/bin/stockfish uv run pytest -m real_model tests/integration/test_maia_provider.py tests/integration/test_maia_realism.py
```

The tests exercise a configured API session and compare Maia's root-policy distribution against a fixed, provenance-recorded human-game reference set and a Stockfish engine constrained to Elo 1500. They have no network access and are excluded from the ordinary local quality gate because the required weights are operator-provided.
