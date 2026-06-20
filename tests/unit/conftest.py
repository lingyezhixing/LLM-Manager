"""Unit-test session bootstrap.

The metering impl layer populates the ``token_parsers`` and ``endpoint_shapes``
registries as an import side effect (the @token_parser seam, spec §8). Importing
it here ensures the registries are populated for every unit test regardless of
import order in individual test modules.
"""

from llm_manager.metering import parsers  # noqa: F401 — registration side effect
