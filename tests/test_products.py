from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user, get_product_service
from app.main import app
from app.models.product import Product
from app.models.user import User, UserRole
from app.repositories.product import DuplicateProductSkuError
from app.services.product import ProductService


class InMemoryProductRepository:
    def __init__(self) -> None:
        self.products: dict[int, Product] = {}
        self._next_id = 1

    def get_by_id(self, product_id: int) -> Product | None:
        return self.products.get(product_id)

    def get_by_sku(self, sku: str) -> Product | None:
        return next(
            (product for product in self.products.values() if product.sku == sku),
            None,
        )

    def create(
        self,
        *,
        sku: str,
        name: str,
        description: str | None,
        price: Decimal,
        stock: int,
        is_active: bool,
    ) -> Product:
        if self.get_by_sku(sku) is not None:
            raise DuplicateProductSkuError
        now = datetime.now(UTC)
        product = Product(
            id=self._next_id,
            sku=sku,
            name=name,
            description=description,
            price=price,
            stock=stock,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )
        self.products[product.id] = product
        self._next_id += 1
        return product

    def list_page(self, *, offset: int, limit: int) -> tuple[list[Product], int]:
        products = sorted(self.products.values(), key=lambda product: product.id)
        return products[offset : offset + limit], len(products)

    def update(self, product: Product, changes: dict[str, object]) -> Product:
        if "sku" in changes:
            existing = self.get_by_sku(str(changes["sku"]))
            if existing is not None and existing.id != product.id:
                raise DuplicateProductSkuError
        for field, value in changes.items():
            setattr(product, field, value)
        product.updated_at = datetime.now(UTC)
        return product

    def delete(self, product: Product) -> None:
        del self.products[product.id]


@pytest.fixture
def product_context() -> Generator[tuple[TestClient, InMemoryProductRepository]]:
    repository = InMemoryProductRepository()
    service = ProductService(repository)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    authenticated_user = User(
        id=1,
        full_name="Authenticated User",
        email="user@example.com",
        hashed_password="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    app.dependency_overrides[get_product_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: authenticated_user

    with TestClient(app) as client:
        yield client, repository

    app.dependency_overrides.pop(get_product_service, None)
    app.dependency_overrides.pop(get_current_user, None)


def create_product(
    client: TestClient,
    *,
    sku: str = "Sku-Mixed-01",
    name: str = "  Precision Product  ",
    description: str | None = "Product description",
    price: str = "19.90",
    stock: int = 5,
    is_active: bool = True,
) -> dict:
    response = client.post(
        "/api/v1/products",
        json={
            "sku": sku,
            "name": name,
            "description": description,
            "price": price,
            "stock": stock,
            "is_active": is_active,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_products_require_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/products")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_create_product_preserves_sku_and_decimal_price(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, repository = product_context

    body = create_product(client)

    assert body["sku"] == "Sku-Mixed-01"
    assert body["name"] == "Precision Product"
    assert body["price"] == "19.90"
    assert body["stock"] == 5
    assert body["is_active"] is True
    saved = repository.get_by_id(body["id"])
    assert saved is not None
    assert saved.price == Decimal("19.90")
    assert isinstance(saved.price, Decimal)


def test_create_product_uses_model_defaults(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context

    response = client.post(
        "/api/v1/products",
        json={"sku": "DEFAULTS-1", "name": "Defaults", "price": "1.00"},
    )

    assert response.status_code == 201
    assert response.json()["description"] is None
    assert response.json()["stock"] == 0
    assert response.json()["is_active"] is True


@pytest.mark.parametrize("price", ["0.01", "19.90", "199.99"])
def test_product_price_preserves_representative_decimal_values(
    product_context: tuple[TestClient, InMemoryProductRepository],
    price: str,
) -> None:
    client, repository = product_context

    body = create_product(client, sku=f"PRICE-{price}", price=price)

    assert body["price"] == price
    saved = repository.get_by_id(body["id"])
    assert saved is not None
    assert saved.price == Decimal(price)


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Missing SKU", "price": "1.00"},
        {"sku": "MISSING-PRICE", "name": "Missing price"},
        {"sku": "NEGATIVE-PRICE", "name": "Invalid", "price": "-0.01"},
        {"sku": "SCALE", "name": "Invalid", "price": "19.999"},
        {"sku": "PRECISION", "name": "Invalid", "price": "12345678901.23"},
        {"sku": "NEGATIVE-STOCK", "name": "Invalid", "price": "1", "stock": -1},
        {
            "sku": "STOCK-OVERFLOW",
            "name": "Invalid",
            "price": "1",
            "stock": 2_147_483_648,
        },
        {"sku": "", "name": "Invalid", "price": "1.00"},
        {"sku": "UNKNOWN", "name": "Invalid", "price": "1", "unknown": 1},
    ],
)
def test_create_product_rejects_invalid_payload(
    product_context: tuple[TestClient, InMemoryProductRepository],
    payload: dict[str, object],
) -> None:
    client, _ = product_context

    response = client.post("/api/v1/products", json=payload)

    assert response.status_code == 422


def test_create_product_rejects_duplicate_exact_sku(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context
    create_product(client, sku="CASE-SENSITIVE")

    response = client.post(
        "/api/v1/products",
        json={"sku": "CASE-SENSITIVE", "name": "Duplicate", "price": "1.00"},
    )

    assert response.status_code == 409
    assert client.get("/api/v1/products").status_code == 200


def test_sku_uniqueness_preserves_database_case_sensitivity(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context
    create_product(client, sku="Exact-SKU")

    body = create_product(client, sku="exact-sku")

    assert body["sku"] == "exact-sku"


def test_list_products_is_empty(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context

    response = client.get("/api/v1/products")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "pages": 0,
    }


def test_list_products_uses_deterministic_database_style_pagination(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context
    for index in range(1, 6):
        create_product(
            client,
            sku=f"SKU-{index}",
            name=f"Product {index}",
            price=f"{index}.00",
        )

    response = client.get("/api/v1/products?page=2&page_size=2")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [3, 4]
    assert body["total"] == 5
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert body["pages"] == 3


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101"])
def test_list_products_validates_pagination_limits(
    product_context: tuple[TestClient, InMemoryProductRepository],
    query: str,
) -> None:
    client, _ = product_context

    response = client.get(f"/api/v1/products?{query}")

    assert response.status_code == 422


def test_get_existing_product(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context
    created = create_product(client)

    response = client.get(f"/api/v1/products/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_missing_product_returns_404(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context

    response = client.get("/api/v1/products/999")

    assert response.status_code == 404


def test_patch_product_updates_one_field_and_preserves_omitted_fields(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context
    created = create_product(client)

    response = client.patch(
        f"/api/v1/products/{created['id']}",
        json={"name": "  Updated name  "},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Updated name"
    for field in ["sku", "description", "price", "stock", "is_active"]:
        assert response.json()[field] == created[field]


def test_patch_product_updates_multiple_fields_with_decimal_precision(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, repository = product_context
    created = create_product(client)

    response = client.patch(
        f"/api/v1/products/{created['id']}",
        json={"description": None, "price": "29.95", "stock": 12},
    )

    assert response.status_code == 200
    assert response.json()["description"] is None
    assert response.json()["price"] == "29.95"
    assert response.json()["stock"] == 12
    saved = repository.get_by_id(created["id"])
    assert saved is not None
    assert saved.price == Decimal("29.95")


def test_patch_missing_product_returns_404(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context

    response = client.patch("/api/v1/products/999", json={"stock": 1})

    assert response.status_code == 404


@pytest.mark.parametrize("price", ["-0.01", "10.999"])
def test_patch_product_rejects_invalid_price(
    product_context: tuple[TestClient, InMemoryProductRepository],
    price: str,
) -> None:
    client, _ = product_context
    created = create_product(client)

    response = client.patch(
        f"/api/v1/products/{created['id']}",
        json={"price": price},
    )

    assert response.status_code == 422


def test_patch_product_rejects_duplicate_sku(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context
    first = create_product(client, sku="SKU-FIRST")
    second = create_product(client, sku="SKU-SECOND")

    response = client.patch(
        f"/api/v1/products/{second['id']}",
        json={"sku": first["sku"]},
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"sku": None},
        {"name": None},
        {"price": None},
        {"stock": None},
        {"is_active": None},
        {"id": 2},
    ],
)
def test_patch_product_rejects_null_required_and_unknown_fields(
    product_context: tuple[TestClient, InMemoryProductRepository],
    payload: dict[str, object],
) -> None:
    client, _ = product_context
    created = create_product(client)

    response = client.patch(
        f"/api/v1/products/{created['id']}",
        json=payload,
    )

    assert response.status_code == 422


def test_delete_product_removes_record(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, repository = product_context
    created = create_product(client)

    response = client.delete(f"/api/v1/products/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert repository.get_by_id(created["id"]) is None
    assert client.get(f"/api/v1/products/{created['id']}").status_code == 404


def test_delete_missing_product_returns_404(
    product_context: tuple[TestClient, InMemoryProductRepository],
) -> None:
    client, _ = product_context

    response = client.delete("/api/v1/products/999")

    assert response.status_code == 404


def test_product_openapi_documents_security_schemas_and_status_codes() -> None:
    schema = app.openapi()
    collection = schema["paths"]["/api/v1/products"]
    resource = schema["paths"]["/api/v1/products/{product_id}"]

    assert set(collection) == {"get", "post"}
    assert set(resource) == {"get", "patch", "delete"}
    for operation in [*collection.values(), *resource.values()]:
        assert operation["security"] == [{"OAuth2PasswordBearer": []}]

    assert {"201", "401", "409", "422"} <= set(collection["post"]["responses"])
    assert {"200", "401", "404", "422"} <= set(resource["get"]["responses"])
    assert {"200", "401", "404", "409", "422"} <= set(resource["patch"]["responses"])
    assert {"204", "401", "404", "409", "422"} <= set(resource["delete"]["responses"])
    assert {
        "ProductCreate",
        "ProductListResponse",
        "ProductResponse",
        "ProductUpdate",
    } <= set(schema["components"]["schemas"])
