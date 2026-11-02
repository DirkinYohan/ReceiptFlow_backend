from rest_framework.permissions import BasePermission, SAFE_METHODS
from django.conf import settings


class IsOwnerOrAdmin(BasePermission):
    owner_field = 'user'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        # Compare ids: reading obj.user would cost a query (or a JOIN) per object.
        owner_id = getattr(obj, f'{self.owner_field}_id', None)
        if owner_id is None:
            owner = getattr(obj, self.owner_field, None)
            owner_id = getattr(owner, 'pk', None)
        return owner_id is not None and owner_id == request.user.pk


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
