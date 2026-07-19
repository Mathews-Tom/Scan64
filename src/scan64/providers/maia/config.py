from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


class MaiaConfigurationError(RuntimeError):
    """Raised when Maia is selected without a usable operator configuration."""


@dataclass(frozen=True)
class MaiaCheckpoint:
    rating: int
    weights_path: Path


@dataclass(frozen=True)
class MaiaSelection:
    checkpoint: MaiaCheckpoint
    requested_rating: int
    disclosure: str | None


@dataclass(frozen=True)
class MaiaConfig:
    binary_path: Path
    checkpoints: tuple[MaiaCheckpoint, ...]
    threads: int = 1

    @classmethod
    def from_toml(cls, path: Path) -> MaiaConfig:
        if not path.is_absolute():
            raise MaiaConfigurationError("Maia configuration path must be absolute")
        try:
            with path.open("rb") as config_file:
                raw_config = tomllib.load(config_file)
        except FileNotFoundError as error:
            raise MaiaConfigurationError(f"Maia configuration does not exist: {path}") from error
        except tomllib.TOMLDecodeError as error:
            raise MaiaConfigurationError(f"Invalid Maia configuration: {path}") from error

        raw_maia = raw_config.get("maia")
        if not isinstance(raw_maia, dict):
            raise MaiaConfigurationError("Maia configuration requires a [maia] table")

        raw_binary_path = raw_maia.get("binary_path")
        raw_threads = raw_maia.get("threads", 1)
        raw_checkpoints = raw_maia.get("checkpoints")
        if not isinstance(raw_binary_path, str) or not raw_binary_path:
            raise MaiaConfigurationError("Maia configuration requires maia.binary_path")
        if not isinstance(raw_threads, int) or not 1 <= raw_threads <= 128:
            raise MaiaConfigurationError("maia.threads must be an integer from 1 through 128")
        if not isinstance(raw_checkpoints, dict) or not raw_checkpoints:
            raise MaiaConfigurationError("Maia configuration requires [maia.checkpoints]")

        checkpoints: list[MaiaCheckpoint] = []
        for raw_rating, raw_weights_path in raw_checkpoints.items():
            try:
                rating = int(raw_rating)
            except ValueError as error:
                raise MaiaConfigurationError(
                    f"Maia checkpoint rating must be an integer: {raw_rating!r}"
                ) from error
            if not isinstance(raw_weights_path, str) or not raw_weights_path:
                raise MaiaConfigurationError(
                    f"Maia checkpoint {rating} requires a non-empty weights path"
                )
            weights_path = Path(raw_weights_path)
            if not weights_path.is_absolute():
                raise MaiaConfigurationError(
                    f"Maia checkpoint {rating} weights path must be absolute"
                )
            checkpoints.append(MaiaCheckpoint(rating=rating, weights_path=weights_path))

        sorted_checkpoints = tuple(sorted(checkpoints, key=lambda checkpoint: checkpoint.rating))
        if len({checkpoint.rating for checkpoint in sorted_checkpoints}) != len(sorted_checkpoints):
            raise MaiaConfigurationError("Maia checkpoint ratings must be unique")

        binary_path = Path(raw_binary_path)
        if not binary_path.is_absolute():
            raise MaiaConfigurationError("maia.binary_path must be absolute")
        return cls(binary_path=binary_path, checkpoints=sorted_checkpoints, threads=raw_threads)

    def select(self, requested_rating: int) -> MaiaSelection:
        lowest = self.checkpoints[0]
        highest = self.checkpoints[-1]
        if requested_rating < lowest.rating:
            return MaiaSelection(
                checkpoint=lowest,
                requested_rating=requested_rating,
                disclosure=(
                    f"Maia has no checkpoint below {lowest.rating}; requested rating "
                    f"{requested_rating} uses the {lowest.rating} checkpoint."
                ),
            )
        if requested_rating > highest.rating:
            return MaiaSelection(
                checkpoint=highest,
                requested_rating=requested_rating,
                disclosure=(
                    f"Maia has no checkpoint above {highest.rating}; requested rating "
                    f"{requested_rating} uses the {highest.rating} checkpoint."
                ),
            )
        checkpoint = min(
            self.checkpoints,
            key=lambda candidate: (
                abs(candidate.rating - requested_rating),
                candidate.rating,
            ),
        )
        disclosure = None
        if requested_rating != checkpoint.rating:
            disclosure = (
                "Maia checkpoints use approximately 100-Elo granularity; requested "
                f"rating {requested_rating} uses the nearest {checkpoint.rating} checkpoint."
            )
        return MaiaSelection(
            checkpoint=checkpoint,
            requested_rating=requested_rating,
            disclosure=disclosure,
        )

    def validate_runtime(self, selection: MaiaSelection) -> None:
        if not self.binary_path.is_file():
            raise MaiaConfigurationError(f"Maia Lc0 binary does not exist: {self.binary_path}")
        if not self.weights_path_is_usable(selection.checkpoint.weights_path):
            raise MaiaConfigurationError(
                "Maia checkpoint does not exist or is not a regular file: "
                f"{selection.checkpoint.weights_path}"
            )

    @staticmethod
    def weights_path_is_usable(weights_path: Path) -> bool:
        return weights_path.is_file()
