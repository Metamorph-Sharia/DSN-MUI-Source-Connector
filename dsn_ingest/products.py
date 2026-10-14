"""DSN-MUI publishes three products. Each becomes its own source connector.

Separate on purpose: fatwa = the rulings, pedoman = how to implement them,
ta'limat (surat edaran) = later changes. Each has its own numbering series,
its own cursor, and its own id prefix, so they never collide.
"""
from dataclasses import dataclass

from . import config


@dataclass(frozen=True)
class Product:
    key: str             # internal key
    connector_name: str  # the interface `name`, unique across the team
    listing_url: str
    paginated: bool      # fatwa lists 17 pages; pedoman and ta'limat are single pages
    id_prefix: str = ""  # fatwa ids stay unprefixed (already in the archive)

    def page_url(self, n: int) -> str:
        if not self.paginated or n == 1:
            return self.listing_url
        return config.page_url(n)


PRODUCTS = {
    "fatwa": Product(
        key="fatwa", connector_name="dsn-fatwa",
        listing_url=config.FATWA_LIST_URL, paginated=True),
    "pedoman": Product(
        key="pedoman", connector_name="dsn-pedoman",
        listing_url="https://dsnmui.or.id/produk/pedoman-implementasi/",
        paginated=False, id_prefix="pedoman-"),
    "talimat": Product(
        key="talimat", connector_name="dsn-talimat",
        listing_url="https://dsnmui.or.id/produk/talimat/",
        paginated=False, id_prefix="talimat-"),
}
