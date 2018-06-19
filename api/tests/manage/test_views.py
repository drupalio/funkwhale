import pytest
from django.urls import reverse

from funkwhale_api.manage import serializers, views


@pytest.mark.parametrize(
    "view,permissions,operator",
    [
        (views.ManageTrackFileViewSet, ["library"], "and"),
        (views.ManageUserViewSet, ["settings"], "and"),
    ],
)
def test_permissions(assert_user_permission, view, permissions, operator):
    assert_user_permission(view, permissions, operator)


def test_track_file_view(factories, superuser_api_client):
    tfs = factories["music.TrackFile"].create_batch(size=5)
    qs = tfs[0].__class__.objects.order_by("-creation_date")
    url = reverse("api:v1:manage:library:track-files-list")

    response = superuser_api_client.get(url, {"sort": "-creation_date"})
    expected = serializers.ManageTrackFileSerializer(
        qs, many=True, context={"request": response.wsgi_request}
    ).data

    assert response.data["count"] == len(tfs)
    assert response.data["results"] == expected


def test_user_view(factories, superuser_api_client, mocker):
    mocker.patch("funkwhale_api.users.models.User.record_activity")
    users = factories["users.User"].create_batch(size=5) + [superuser_api_client.user]
    qs = users[0].__class__.objects.order_by("-id")
    url = reverse("api:v1:manage:users:users-list")

    response = superuser_api_client.get(url, {"sort": "-id"})
    expected = serializers.ManageUserSerializer(
        qs, many=True, context={"request": response.wsgi_request}
    ).data

    assert response.data["count"] == len(users)
    assert response.data["results"] == expected
