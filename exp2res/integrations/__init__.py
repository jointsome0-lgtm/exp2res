"""§19 source-local importers, keyed by the §14.5 source system."""

from exp2res.integrations import atlas, ephemeris, github
from exp2res.integrations.records import SourceContract

CONTRACTS: dict[str, SourceContract] = {
    contract.source_system: contract
    for contract in (ephemeris.CONTRACT, atlas.CONTRACT, github.CONTRACT)
}

__all__ = ["CONTRACTS", "atlas", "ephemeris", "github"]
