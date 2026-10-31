from rest_framework.permissions import BasePermission, SAFE_METHODS
from django.conf import settings


class IsOwnerOrAdmin(BasePermission):
    owner_field = 'user'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        owner = getattr(obj, self.owner_field, None)
        return owner == request.user


class IsStaffOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return bool(request.user and request.user.is_staff)


class IsVerifiedUser(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if not settings.EMAIL_VERIFICATION_REQUIRED:
            return True
        return getattr(request.user, 'is_email_verified', False)
