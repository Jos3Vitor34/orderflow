from app.models.product import Product
from app.repositories.product import DuplicateProductSkuError, ProductRepository
from app.schemas.product import ProductCreate, ProductListResponse, ProductUpdate


class ProductNotFoundError(Exception):
    """Raised when a product identifier does not exist."""


class ProductService:
    def __init__(self, repository: ProductRepository) -> None:
        self._repository = repository

    def create(self, data: ProductCreate) -> Product:
        if self._repository.get_by_sku(data.sku) is not None:
            raise DuplicateProductSkuError
        return self._repository.create(
            sku=data.sku,
            name=data.name,
            description=data.description,
            price=data.price,
            stock=data.stock,
            is_active=data.is_active,
        )

    def list(self, *, page: int, page_size: int) -> ProductListResponse:
        products, total = self._repository.list_page(
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        pages = (total + page_size - 1) // page_size
        return ProductListResponse(
            items=products,
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def get(self, product_id: int) -> Product:
        product = self._repository.get_by_id(product_id)
        if product is None:
            raise ProductNotFoundError
        return product

    def update(self, product_id: int, data: ProductUpdate) -> Product:
        product = self.get(product_id)
        changes = data.model_dump(exclude_unset=True)
        if "sku" in changes:
            existing = self._repository.get_by_sku(str(changes["sku"]))
            if existing is not None and existing.id != product.id:
                raise DuplicateProductSkuError
        return self._repository.update(product, changes)

    def delete(self, product_id: int) -> None:
        self._repository.delete(self.get(product_id))
