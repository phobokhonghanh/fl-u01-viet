"""Fixtures for offline testing of Fotello."""
from __future__ import annotations

MOCK_TOKENS = {
    "access_token": "mock_access_tok_123",
    "id_token": "header.eyJlbWFpbCI6ICJ0ZXN0QGZvdGVsbG8uY28iLCAiZXhwIjogMjAwMDAwMDAwMCwgInVzZXJfaWQiOiAidXNyXzEifQ.signature",
    "refresh_token": "mock_refresh_tok_123",
    "user_id": "usr_1",
    "team_id": "team_1",
    "connected": True,
}

# Listing with both standard edited (2048x1365) and upsized (8192x5464)
MOCK_ENHANCE_DOCS = [
    {
        "document": {
            "name": "projects/real-estate-firebase-4109e/databases/(default)/documents/enhances/enh_001",
            "fields": {
                "listingId": {"stringValue": "listing_test_123"},
                "status": {"stringValue": "completed"},
                "editedImage": {"stringValue": "gs://mock-bucket/renders/enh_001_standard.jpg"},
                "editedImageUpsized": {"stringValue": "gs://mock-bucket/renders/enh_001_upsized.jpg"},
                "inputFilenames": {"arrayValue": {"values": [{"stringValue": "living_room.jpg"}]}},
                "sourceFilenames": {"arrayValue": {"values": [{"stringValue": "living_room.jpg"}]}},
                "inputWidth": {"integerValue": "8192"},
                "inputHeight": {"integerValue": "5464"},
            }
        }
    },
    {
        "document": {
            "name": "projects/real-estate-firebase-4109e/databases/(default)/documents/enhances/enh_002",
            "fields": {
                "listingId": {"stringValue": "listing_test_123"},
                "status": {"stringValue": "completed"},
                # Only standard image available (no upsized generated)
                "editedImage": {"stringValue": "gs://mock-bucket/renders/enh_002_standard.jpg"},
                "inputFilenames": {"arrayValue": {"values": [{"stringValue": "bedroom.jpg"}]}},
                "sourceFilenames": {"arrayValue": {"values": [{"stringValue": "bedroom.jpg"}]}},
                "inputWidth": {"integerValue": "6000"},
                "inputHeight": {"integerValue": "4000"},
            }
        }
    }
]

MOCK_LISTING_DOCS = [
    {
        "document": {
            "name": "projects/real-estate-firebase-4109e/databases/(default)/documents/listings/listing_test_123",
            "fields": {
                "name": {"stringValue": "123 Villa Luxury"},
                "status": {"stringValue": "completed"},
                "createdAt": {"stringValue": "2026-09-09T10:00:00.000Z"},
            }
        }
    }
]

# Mock enhance doc with all 4 prompt example renditions: edited, edited_upsized, merged, merged_upsized
MOCK_MULTI_RENDITION_DOCS = [
    {
        "document": {
            "name": "projects/real-estate-firebase-4109e/databases/(default)/documents/enhances/enh_abc123",
            "fields": {
                "listingId": {"stringValue": "listing_multi_123"},
                "status": {"stringValue": "completed"},
                "editedImage": {"stringValue": "gs://mock-bucket/renders/phong_khach_edited.jpg"},
                "editedImageUpsized": {"stringValue": "gs://mock-bucket/renders/phong_khach_edited_upsized.jpg"},
                "mergedImage": {"stringValue": "gs://mock-bucket/renders/phong_khach_merged.jpg"},
                "mergedImageUpsized": {"stringValue": "gs://mock-bucket/renders/phong_khach_merged_upsized.jpg"},
                "inputFilenames": {"arrayValue": {"values": [{"stringValue": "phong_khach.jpg"}]}},
                "sourceFilenames": {"arrayValue": {"values": [{"stringValue": "phong_khach.jpg"}]}},
                "inputWidth": {"integerValue": "6000"},
                "inputHeight": {"integerValue": "4000"},
            }
        }
    }
]

MOCK_VARIANT_DOCS = [
    {
        "document": {
            "name": "projects/real-estate-firebase-4109e/databases/(default)/documents/variants/var_sky01",
            "fields": {
                "listingId": {"stringValue": "listing_multi_123"},
                "parentId": {"stringValue": "enh_abc123"},
                "type": {"stringValue": "sky_replacement"},
                "activeRenderId": {"stringValue": "rnd_v1"},
                "renders": {
                    "mapValue": {
                        "fields": {
                            "rnd_v1": {
                                "mapValue": {
                                    "fields": {
                                        "status": {"stringValue": "success"},
                                        "outputs": {
                                            "arrayValue": {
                                                "values": [
                                                    {
                                                        "mapValue": {
                                                            "fields": {
                                                                "uri": {"stringValue": "gs://mock-bucket/renders/rnd_v1_output.jpg"},
                                                                "upsizedUri": {"stringValue": "gs://mock-bucket/renders/rnd_v1_upsized.jpg"},
                                                            }
                                                        }
                                                    }
                                                ]
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
]
