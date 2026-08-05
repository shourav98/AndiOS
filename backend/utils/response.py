from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel

T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    """
    Standardized API Response Model for FastAPI.
    All routes should use response_model=ApiResponse[YourModel]
    """
    success: bool
    status: int
    message: str
    data: Optional[T] = None


def api_success(data: Any = None, message: str = "Success", status_code: int = 200) -> dict:
    """
    Helper function to format successful API responses.
    Usage: return api_success(data=my_data, message="Fetched successfully")
    """
    return {
        "success": True,
        "status": status_code,
        "message": message,
        "data": data
    }


def api_error(message: str, status_code: int = 400, data: Any = None) -> dict:
    """
    Helper function to format error API responses.
    Usage: return JSONResponse(status_code=400, content=api_error("Bad Request"))
    """
    return {
        "success": False,
        "status": status_code,
        "message": message,
        "data": data
    }
