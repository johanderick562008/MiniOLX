"""JSON API for products."""
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_user_csrf
from app.models import Product, User
from app.schemas import ProductFilters, ProductForm, ProductOut, ProductUpdateForm
from app.services import product_service
from app.services.product_service import InvalidImageError

router = APIRouter(prefix="/api/products", tags=["products"])


def _validated(model: type[BaseModel], **fields) -> BaseModel:
    """Run form fields through a Pydantic model; bad input becomes a 422,
    exactly like a JSON body would."""
    try:
        return model(**fields)
    except ValidationError as exc:
        errors = [{**err, "loc": ("body", *err["loc"])} for err in exc.errors()]
        raise RequestValidationError(errors)


# These two dependencies read the form fields. The file is read separately,
# because FastAPI can't mix a form model and a file in one parameter.
def product_create_form(
    name: Annotated[str, Form()], description: Annotated[str, Form()],
    price: Annotated[str, Form()], category: Annotated[str, Form()],
    condition: Annotated[str, Form()], quantity: Annotated[str, Form()],
) -> ProductForm:
    return _validated(ProductForm, name=name, description=description, price=price,
                      category=category, condition=condition, quantity=quantity)


def product_update_form(
    name: Annotated[str, Form()], description: Annotated[str, Form()],
    price: Annotated[str, Form()], category: Annotated[str, Form()],
    condition: Annotated[str, Form()], quantity: Annotated[str, Form()],
) -> ProductUpdateForm:
    return _validated(ProductUpdateForm, name=name, description=description, price=price,
                      category=category, condition=condition, quantity=quantity)


def _get_or_404(db: Session, product_id: int) -> Product:
    product = product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


def _get_owned_or_403(db: Session, product_id: int, user: User) -> Product:
    """The ownership check. This, not a hidden Edit button, is what stops
    User A from editing or deleting User B's listing."""
    product = _get_or_404(db, product_id)
    if product.seller_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only change your own listings.")
    return product


def _save_image_or_400(image: UploadFile) -> str:
    try:
        return product_service.save_product_image(image)
    except InvalidImageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("", response_model=list[ProductOut])
def list_products(filters: Annotated[ProductFilters, Query()], db: Session = Depends(get_db)):
    """Examples:
    /api/products?search=keyboard
    /api/products?category=electronics&min_price=500&max_price=3000&sort=price_asc
    """
    return product_service.search_products(db, filters)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, product_id)


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    image: Annotated[UploadFile, File()],
    data: ProductForm = Depends(product_create_form),
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
):
    if not user.accepts_upi:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Add your UPI ID in Payment settings before listing items, so buyers can pay you.")
    filename = _save_image_or_400(image)
    return product_service.create_product(db, user, data, filename)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    image: Annotated[UploadFile | None, File()] = None,
    data: ProductUpdateForm = Depends(product_update_form),
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
):
    product = _get_owned_or_403(db, product_id, user)
    new_filename = _save_image_or_400(image) if image and image.filename else None
    return product_service.update_product(db, product, data, new_filename)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(
    product_id: int,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
):
    product = _get_owned_or_403(db, product_id, user)
    product_service.delete_product(db, product)
