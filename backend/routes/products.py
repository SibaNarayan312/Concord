from fastapi import APIRouter, status, Query
from typing import List, Optional
from backend.models.schemas import ProductCreate, ProductUpdate, ProductResponse
from backend.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["Products"])

@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(payload: ProductCreate):
    return await ProductService.create_product(payload)

@router.get("", response_model=List[ProductResponse])
async def list_products():
    return await ProductService.list_products()

@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(product_id: str):
    return await ProductService.get_product(product_id)

@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(product_id: str, payload: ProductUpdate):
    return await ProductService.update_product(product_id, payload)

@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: str):
    await ProductService.delete_product(product_id)

@router.post("/{product_id}/reset", response_model=ProductResponse)
async def reset_product_inventory(
    product_id: str,
    stock: Optional[int] = Query(None, ge=0, description="Optional new stock value to reset to")
):
    """Controlled test helper: resets product inventory and expires dangling holds."""
    return await ProductService.reset_inventory(product_id, new_stock=stock)
