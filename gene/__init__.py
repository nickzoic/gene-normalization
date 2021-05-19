"""The VICC library for normalizing genes."""
from pathlib import Path
import logging

PROJECT_ROOT = Path(__file__).resolve().parents[0]
logging.basicConfig(
    filename='gene.log',
    format='[%(asctime)s] %(levelname)s : %(message)s')
logger = logging.getLogger('gene')
logger.setLevel(logging.DEBUG)


__version__ = "0.1.10"


class DownloadException(Exception):
    """Exception for failures relating to source file downloads."""

    def __init__(self, *args, **kwargs):
        """Initialize exception."""
        super().__init__(*args, **kwargs)


from gene.schemas import SourceName, NamespacePrefix, SourceIDAfterNamespace, ItemTypes  # noqa: E402, E501
ITEM_TYPES = {k.lower(): v.value for k, v in ItemTypes.__members__.items()}

# Sources we import directly (HGNC, Ensembl, NCBI)
SOURCES = {source.value.lower(): source.value
           for source in SourceName.__members__.values()}

# Set of sources we import directly
XREF_SOURCES = {src.lower() for src in SourceName.__members__}

# use to fetch source name from schema based on concept id namespace
# e.g. {'hgnc': 'HGNC'}
PREFIX_LOOKUP = {v.value: SourceName[k].value
                 for k, v in NamespacePrefix.__members__.items()
                 if k in SourceName.__members__.keys()}

# use to generate namespace prefix from source ID value
# e.g. {'ensg': 'ensembl'}
NAMESPACE_LOOKUP = {v.value.lower(): NamespacePrefix[k].value
                    for k, v in SourceIDAfterNamespace.__members__.items()
                    if v.value != ''}

from gene.etl import NCBI, HGNC, Ensembl  # noqa: E402, F401
# used to get source class name from string
SOURCES_CLASS = \
    {s.value.lower(): eval(s.value) for s in SourceName.__members__.values()}
