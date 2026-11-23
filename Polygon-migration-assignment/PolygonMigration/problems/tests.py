<<<<<<< Updated upstream
from django.test import TestCase

# Create your tests here.
=======
from io import StringIO
from unittest import mock

import boto3
from botocore.stub import Stubber
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from .polygon_api import PolygonAPI
from .storage import StorageBackend, StorageError, get_storage, test_case_keys
from .storage.azure import AzureBlobStorageBackend
from .storage.s3 import S3StorageBackend


class MigrationsInSyncTest(TestCase):
    def test_no_model_changes_without_migrations(self):
        """Every model field must have a migration, or the DB is missing columns at runtime."""
        out = StringIO()
        try:
            call_command('makemigrations', '--check', '--dry-run', stdout=out, stderr=StringIO())
        except SystemExit:
            self.fail(f"Models have changes with no migration:\n{out.getvalue()}")


class FakeStorage(StorageBackend):
    """In-memory backend that records the order of operations."""

    name = "Fake"

    def __init__(self, objects=None, fail_on_upload=False, fail_after_uploads=None, fail_on_delete=False):
        self.objects = dict(objects or {})
        self.operations = []
        self.fail_on_upload = fail_on_upload
        self.fail_after_uploads = fail_after_uploads  # succeed this many uploads, then fail
        self.fail_on_delete = fail_on_delete

    def upload(self, key, data):
        uploads_done = sum(1 for op, _ in self.operations if op == "upload")
        if self.fail_on_upload or (self.fail_after_uploads is not None and uploads_done >= self.fail_after_uploads):
            raise StorageError(f"upload failed for {key}")
        self.operations.append(("upload", key))
        self.objects[key] = data

    def list_keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def delete_prefix(self, prefix):
        if self.fail_on_delete and self.operations:  # the first delete (before uploading) succeeds
            raise StorageError(f"delete failed for {prefix}")
        keys = self.list_keys(prefix)
        for k in keys:
            del self.objects[k]
        self.operations.append(("delete_prefix", prefix))
        return len(keys)


class TestCaseKeysTest(SimpleTestCase):
    def test_keys_match_required_layout(self):
        self.assertEqual(test_case_keys(7, 1), ("test_cases/7/1", "test_cases/7/1.a"))
        self.assertEqual(test_case_keys(7, 14), ("test_cases/7/14", "test_cases/7/14.a"))


class MigrateToStorageTest(SimpleTestCase):
    TESTS = [
        {"input": "3\n1 2 3\n", "output": "6\n"},
        {"input": "", "output": "0\n"},  # missing input: skipped, but keeps its number
        {"input": "1\n-5\n", "output": "-5\n"},
    ]

    def setUp(self):
        self.api = PolygonAPI()
        patches = [
            mock.patch.object(PolygonAPI, "get_test_cases_from_redis", return_value=self.TESTS),
            mock.patch.object(PolygonAPI, "get_custom_checker_info", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_replaces_old_files_and_uploads_input_output_pairs(self):
        storage = FakeStorage({
            "test_cases/7/99": b"stale",           # old file for this problem: must be removed
            "test_cases/70/1": b"other problem",   # shares the "7" prefix text: must survive
        })

        uploaded = self.api.migrate_to_storage("636832", storage, db_problem_id=7)

        self.assertEqual(uploaded, 2)
        self.assertEqual(storage.operations[0], ("delete_prefix", "test_cases/7/"))
        self.assertEqual(storage.objects, {
            "test_cases/70/1": b"other problem",
            "test_cases/7/1": b"3\n1 2 3\n",
            "test_cases/7/1.a": b"6\n",
            "test_cases/7/3": b"1\n-5\n",
            "test_cases/7/3.a": b"-5\n",
        })

    def test_upload_failure_is_raised_not_swallowed(self):
        with self.assertRaises(StorageError):
            self.api.migrate_to_storage("636832", FakeStorage(fail_on_upload=True), db_problem_id=7)

    def test_rollback_deletes_problem_files_and_never_raises(self):
        storage = FakeStorage({"test_cases/7/1": b"x", "test_cases/7/1.a": b"y"})
        self.assertTrue(self.api.delete_test_case_files(storage, 7))
        self.assertEqual(storage.objects, {})

        broken = mock.Mock(spec=StorageBackend)
        broken.delete_prefix.side_effect = StorageError("boom")
        self.assertFalse(self.api.delete_test_case_files(broken, 7))  # logged, not raised


S3_SETTINGS = dict(
    STORAGE_PROVIDER="s3",
    S3_BUCKET_NAME="bucket",
    S3_REGION="ap-south-1",
    S3_ACCESS_KEY_ID="key",
    S3_SECRET_ACCESS_KEY="secret",
    S3_ENDPOINT_URL="",
)


class GetStorageTest(SimpleTestCase):
    @override_settings(**S3_SETTINGS)
    def test_s3_provider(self):
        storage = get_storage()
        self.assertIsInstance(storage, S3StorageBackend)
        self.assertEqual(storage.bucket, "bucket")

    @override_settings(**{**S3_SETTINGS, "STORAGE_PROVIDER": " S3 "})
    def test_provider_name_is_case_and_space_insensitive(self):
        self.assertIsInstance(get_storage(), S3StorageBackend)

    @override_settings(**{**S3_SETTINGS, "S3_ENDPOINT_URL": "https://account.r2.cloudflarestorage.com"})
    def test_custom_endpoint_is_used(self):
        self.assertEqual(get_storage().client.meta.endpoint_url, "https://account.r2.cloudflarestorage.com")

    @override_settings(**{**S3_SETTINGS, "S3_BUCKET_NAME": ""})
    def test_missing_settings_are_named_in_the_error(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "needs these environment variables: S3_BUCKET_NAME"):
            get_storage()

    @override_settings(**{**S3_SETTINGS, "S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": None})
    def test_s3_keys_are_optional_for_the_default_credential_chain(self):
        self.assertIsInstance(get_storage(), S3StorageBackend)

    @override_settings(STORAGE_PROVIDER="gcs")
    def test_unknown_provider(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "Unknown STORAGE_PROVIDER 'gcs'"):
            get_storage()

    @override_settings(
        STORAGE_PROVIDER="azure",
        AZURE_STORAGE_ACCOUNT_URL="https://account.blob.core.windows.net",
        AZURE_TENANT_ID="tenant",
        AZURE_CLIENT_ID="client",
        AZURE_USERNAME="user",
        AZURE_PASSWORD="password",
        AZURE_CONTAINER_NAME="testcases",
    )
    def test_azure_provider(self):
        # Building the client makes no network call; only the wiring is checked here.
        self.assertIsInstance(get_storage(), AzureBlobStorageBackend)


class S3StorageBackendTest(SimpleTestCase):
    def setUp(self):
        client = boto3.client("s3", region_name="ap-south-1", aws_access_key_id="key", aws_secret_access_key="secret")
        self.stubber = Stubber(client)
        self.stubber.activate()
        self.addCleanup(self.stubber.deactivate)
        self.storage = S3StorageBackend(bucket="bucket", client=client)

    def test_upload_puts_object(self):
        self.stubber.add_response("put_object", {}, {"Bucket": "bucket", "Key": "test_cases/1/1", "Body": b"3\n"})
        self.storage.upload("test_cases/1/1", b"3\n")
        self.stubber.assert_no_pending_responses()

    def test_delete_prefix_lists_then_deletes(self):
        self.stubber.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "test_cases/1/1"}, {"Key": "test_cases/1/1.a"}], "IsTruncated": False},
            {"Bucket": "bucket", "Prefix": "test_cases/1/"},
        )
        self.stubber.add_response(
            "delete_objects",
            {},
            {"Bucket": "bucket", "Delete": {"Objects": [{"Key": "test_cases/1/1"}, {"Key": "test_cases/1/1.a"}], "Quiet": True}},
        )
        self.assertEqual(self.storage.delete_prefix("test_cases/1/"), 2)
        self.stubber.assert_no_pending_responses()

    def test_delete_prefix_with_nothing_stored(self):
        self.stubber.add_response("list_objects_v2", {"IsTruncated": False}, {"Bucket": "bucket", "Prefix": "test_cases/1/"})
        self.assertEqual(self.storage.delete_prefix("test_cases/1/"), 0)

    def test_aws_errors_become_storage_errors(self):
        self.stubber.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
        with self.assertRaisesMessage(StorageError, "AccessDenied"):
            self.storage.upload("test_cases/1/1", b"3\n")


class S3StorageBackendBatchingTest(SimpleTestCase):
    def setUp(self):
        client = boto3.client("s3", region_name="ap-south-1", aws_access_key_id="key", aws_secret_access_key="secret")
        self.stubber = Stubber(client)
        self.stubber.activate()
        self.addCleanup(self.stubber.deactivate)
        self.storage = S3StorageBackend(bucket="bucket", client=client)

    def test_multi_page_listing_and_more_than_1000_keys_are_all_deleted(self):
        keys = [f"test_cases/1/{i}" for i in range(1001)]
        self.stubber.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": k} for k in keys[:600]], "IsTruncated": True, "NextContinuationToken": "page2"},
            {"Bucket": "bucket", "Prefix": "test_cases/1/"},
        )
        self.stubber.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": k} for k in keys[600:]], "IsTruncated": False},
            {"Bucket": "bucket", "Prefix": "test_cases/1/", "ContinuationToken": "page2"},
        )
        for batch in (keys[:1000], keys[1000:]):
            self.stubber.add_response(
                "delete_objects", {},
                {"Bucket": "bucket", "Delete": {"Objects": [{"Key": k} for k in batch], "Quiet": True}},
            )
        self.assertEqual(self.storage.delete_prefix("test_cases/1/"), 1001)
        self.stubber.assert_no_pending_responses()

    def test_partial_delete_errors_are_raised(self):
        self.stubber.add_response(
            "list_objects_v2", {"Contents": [{"Key": "test_cases/1/1"}], "IsTruncated": False},
            {"Bucket": "bucket", "Prefix": "test_cases/1/"},
        )
        self.stubber.add_response(
            "delete_objects", {"Errors": [{"Key": "test_cases/1/1", "Code": "AccessDenied", "Message": "denied"}]},
            {"Bucket": "bucket", "Delete": {"Objects": [{"Key": "test_cases/1/1"}], "Quiet": True}},
        )
        with self.assertRaisesMessage(StorageError, "S3 delete failed for 1 objects"):
            self.storage.delete_prefix("test_cases/1/")


class AzureBlobStorageBackendTest(SimpleTestCase):
    def setUp(self):
        with mock.patch("problems.storage.azure.UsernamePasswordCredential"), \
             mock.patch("problems.storage.azure.BlobServiceClient") as service:
            self.storage = AzureBlobStorageBackend("https://a.blob.core.windows.net", "t", "c", "u", "p", "testcases")
        self.container = service.return_value.get_container_client.return_value
        service.return_value.get_container_client.assert_called_once_with("testcases")

    def test_upload_overwrites(self):
        self.storage.upload("test_cases/1/1", b"3\n")
        self.container.upload_blob.assert_called_once_with(name="test_cases/1/1", data=b"3\n", overwrite=True)

    def test_delete_prefix_lists_then_deletes_each_blob(self):
        first, second = mock.Mock(), mock.Mock()
        first.name, second.name = "test_cases/1/1", "test_cases/1/1.a"
        self.container.list_blobs.return_value = [first, second]
        self.assertEqual(self.storage.delete_prefix("test_cases/1/"), 2)
        self.container.list_blobs.assert_called_once_with(name_starts_with="test_cases/1/")
        self.container.delete_blob.assert_has_calls([mock.call("test_cases/1/1"), mock.call("test_cases/1/1.a")])


@override_settings(CUSTOM_CHECKER_DIR=None)
class CustomCheckerUploadTest(SimpleTestCase):
    def setUp(self):
        self.api = PolygonAPI()
        patches = [
            mock.patch.object(PolygonAPI, "get_custom_checker_info", return_value={"name": "check.cpp", "type": "custom"}),
            mock.patch.object(PolygonAPI, "fetch_custom_checker_file", return_value="int main(){}"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_source_is_uploaded_when_compilation_fails(self):
        storage = FakeStorage()
        with mock.patch.object(PolygonAPI, "compile_custom_checker", return_value=None):
            self.api.upload_custom_checker("636832", storage, db_problem_id=7)
        self.assertEqual(storage.objects, {"test_cases/7/custom_checker.cpp": b"int main(){}"})

    def test_binary_is_uploaded_when_compilation_succeeds(self):
        storage = FakeStorage()

        def fake_compile(source, work_dir):
            path = f"{work_dir}/custom_checker.bin"
            with open(path, "wb") as f:
                f.write(b"\x7fELF")
            return path

        with mock.patch.object(PolygonAPI, "compile_custom_checker", side_effect=fake_compile), \
             mock.patch("problems.polygon_api.sys.platform", "linux"):
            self.api.upload_custom_checker("636832", storage, db_problem_id=7)
        self.assertEqual(storage.objects, {"test_cases/7/custom_checker": b"\x7fELF"})


class StorageMigrationViewTest(TestCase):
    """Drives the real index view with Polygon mocked and an in-memory storage backend."""

    TESTS = [
        {"index": 1, "input": "3\n1 2 3\n", "output": "6\n", "is_sample": True},
        {"index": 2, "input": "1\n-5\n", "output": "-5\n", "is_sample": False},
    ]
    HTML = '<html><body><div class="title">Array Sum</div><div class="legend"><p>Sum it.</p></div></body></html>'

    def setUp(self):
        from django.contrib.auth import get_user_model
        from .models import Problem

        user = get_user_model().objects.create_user(email="staff@example.com", password="pw", username="staff", is_staff=True)
        self.client.force_login(user)
        self.problem = Problem.objects.create(polygon_id="636832", title="Array Sum", slug="array-sum", difficulty="easy")

        def make_request(method, params=None, expect_json=True):
            return {"problem.checker": "std::ncmp.cpp", "problem.solutions": []}.get(method, {})

        patches = [
            mock.patch.object(PolygonAPI, "_make_request", side_effect=make_request),
            mock.patch.object(PolygonAPI, "_make_plain_request", return_value=""),
            mock.patch.object(PolygonAPI, "get_problem_info", return_value={"name": "Array Sum"}),
            mock.patch.object(PolygonAPI, "get_test_cases_from_redis", return_value=self.TESTS),
            mock.patch.object(PolygonAPI, "get_all_test_cases", return_value=self.TESTS),
            mock.patch.object(PolygonAPI, "get_test_cases", return_value=self.TESTS),
            mock.patch.object(PolygonAPI, "get_custom_checker_info", return_value=None),
            mock.patch.object(PolygonAPI, "store_test_cases_in_redis"),
            mock.patch.object(PolygonAPI, "clear_test_cases_from_redis"),
            mock.patch.object(PolygonAPI, "delete_problem_test_case_cache"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def post_upload(self, storage, package_html=HTML, package_error=None):
        download = mock.patch.object(
            PolygonAPI, "download_and_extract_package", return_value=package_html, side_effect=package_error,
        )
        with download, mock.patch("problems.views.get_storage", return_value=storage):
            return self.client.post("/", {"problem_id": "636832", "migrate_to_storage": "1"})

    def prefix(self):
        return f"test_cases/{self.problem.id}/"

    def test_success_uploads_files_and_reports_count(self):
        storage = FakeStorage()
        response = self.post_upload(storage)
        self.assertEqual(sorted(storage.list_keys(self.prefix())), [f"{self.prefix()}{n}" for n in ("1", "1.a", "2", "2.a")])
        self.assertIn(f"2 test cases uploaded to Fake under {self.prefix()}", response.context["success"])
        self.assertNotIn("error", response.context)

    def test_upload_failure_part_way_removes_partial_files_and_shows_only_an_error(self):
        # Test 1 (input + output) uploads, then test 2's input fails
        storage = FakeStorage({f"{self.prefix()}1": b"old"}, fail_after_uploads=2)
        response = self.post_upload(storage)
        self.assertEqual(storage.list_keys(self.prefix()), [])
        self.assertIn("Uploading test cases to Fake failed, so this problem's files were removed", response.context["error"])
        self.assertNotIn("success", response.context)

    def test_upload_failure_with_failed_cleanup_warns_files_may_remain(self):
        storage = FakeStorage(fail_after_uploads=2, fail_on_delete=True)
        response = self.post_upload(storage)
        self.assertEqual(len(storage.list_keys(self.prefix())), 2)
        self.assertIn("some of this problem's files may remain in storage", response.context["error"])
        self.assertNotIn("success", response.context)

    def test_display_failure_after_upload_keeps_the_uploaded_files(self):
        storage = FakeStorage()
        response = self.post_upload(storage, package_error=Exception("Polygon timed out"))
        self.assertEqual(len(storage.list_keys(self.prefix())), 4)
        self.assertIn("Test cases were uploaded to Fake, but reloading the problem from Polygon failed", response.context["error"])
        self.assertNotIn("rolled back", response.context["error"])
>>>>>>> Stashed changes
