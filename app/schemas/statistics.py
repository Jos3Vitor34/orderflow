from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.order import OrderStatus
from app.models.payment import PaymentStatus


class OrderStatistics(BaseModel):
    total: int
    by_status: dict[OrderStatus, int]
    total_amount: Decimal
    average_ticket: Decimal
    created_in_period: int


class PaymentStatistics(BaseModel):
    total: int
    by_status: dict[PaymentStatus, int]
    total_processed_amount: Decimal
    successful_amount: Decimal
    successful_count: int
    success_rate: Decimal


class OperationalStatistics(BaseModel):
    pending_orders: int
    processing_orders: int
    confirmed_orders: int
    cancelled_orders: int


class StatisticsOverviewResponse(BaseModel):
    orders: OrderStatistics
    payments: PaymentStatistics
    operational: OperationalStatistics
    generated_at: datetime
