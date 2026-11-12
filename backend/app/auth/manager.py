# -*- coding: utf-8 -*-
"""用户账号管理：用户名归一、密码哈希与重复创建保护。"""
from __future__ import annotations

from beanie import PydanticObjectId
from fastapi_users import BaseUserManager
from fastapi_users.exceptions import UserAlreadyExists, UserNotExists
from fastapi_users_db_beanie import BeanieUserDatabase, ObjectIDIDMixin
from pymongo.errors import DuplicateKeyError
from starlette.requests import Request

from app.auth.models import User
from app.auth.schemas import UserCreateRequest


class UserManager(ObjectIDIDMixin, BaseUserManager[User, PydanticObjectId]):
    """统一从 User 文档类构造用户库适配器。"""

    def __init__(self, user_model: type[User] = User) -> None:
        super().__init__(BeanieUserDatabase(user_model))

    async def get_by_email(self, email: str) -> User:
        """按小写用户名查询；不存在时抛 UserNotExists。"""
        model = self.user_db.user_model
        user = await model.find_one(model.email == email.lower())
        if user is None:
            raise UserNotExists()
        return user

    async def create(
        self,
        user_create: UserCreateRequest,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        email = user_create.username.lower()
        try:
            await self.get_by_email(email)
        except UserNotExists:
            pass
        else:
            raise UserAlreadyExists()

        user = User(
            email=email,
            hashed_password=self.password_helper.hash(user_create.password),
            is_verified=True,
            is_active=True,
            is_superuser=user_create.is_admin,
        )
        try:
            await user.insert()
        except DuplicateKeyError:
            # 并发创建竞态：唯一索引兜底后统一转成业务异常。
            raise UserAlreadyExists() from None
        return user


user_manager = UserManager(User)
