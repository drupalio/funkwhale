
from django_filters import rest_framework as filters

from funkwhale_api.common import fields
from funkwhale_api.music import models as music_models
from funkwhale_api.users import models as users_models


class ManageTrackFileFilterSet(filters.FilterSet):
    q = fields.SearchFilter(
        search_fields=[
            "track__title",
            "track__album__title",
            "track__artist__name",
            "source",
        ]
    )

    class Meta:
        model = music_models.TrackFile
        fields = ["q", "track__album", "track__artist", "track", "library_track"]


class ManageUserFilterSet(filters.FilterSet):
    q = fields.SearchFilter(search_fields=["username", "email", "name"])

    class Meta:
        model = users_models.User
        fields = [
            "q",
            "is_active",
            "privacy_level",
            "is_staff",
            "is_superuser",
            "permission_upload",
            "permission_library",
            "permission_settings",
            "permission_federation",
        ]
