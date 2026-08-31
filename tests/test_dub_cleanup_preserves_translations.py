from services.segmentation import clean_up_segments


def test_cleanup_preserves_editor_metadata_and_combines_translations():
    segments = [
        {
            "id": "a",
            "start": 0.0,
            "end": 2.0,
            "text": "Hola, gran mundo.",
            "text_original": "Hello, big world.",
            "speaker_id": "Speaker 1",
            "profile_id": "voice-a",
            "translations": {"es": "Hola, gran mundo.", "fr": "Bonjour le monde."},
        },
        {
            "id": "b",
            "start": 2.1,
            "end": 2.4,
            "text": "Otra vez.",
            "text_original": "Again.",
            "speaker_id": "Speaker 1",
            "profile_id": "voice-b",
            "translations": {"es": "Otra vez.", "fr": "Encore."},
            "translate_error": "retry me",
        },
    ]

    cleaned = clean_up_segments(segments)

    assert len(cleaned) == 1
    assert cleaned[0]["text"] == "Hola, gran mundo. Otra vez."
    assert cleaned[0]["text_original"] == "Hello, big world. Again."
    assert cleaned[0]["translations"] == {
        "es": "Hola, gran mundo. Otra vez.",
        "fr": "Bonjour le monde. Encore.",
    }
    assert cleaned[0]["profile_id"] == "voice-a"
    assert cleaned[0]["translate_error"] == "retry me"


def test_cleanup_ignores_legacy_non_mapping_translations():
    segments = [
        {
            "id": "a",
            "start": 0.0,
            "end": 2.0,
            "text": "Hello.",
            "speaker_id": "Speaker 1",
            "translations": "legacy-corrupt-value",
        },
        {
            "id": "b",
            "start": 2.1,
            "end": 2.4,
            "text": "Again.",
            "speaker_id": "Speaker 1",
            "translations": {"es": "Otra vez."},
        },
    ]

    cleaned = clean_up_segments(segments)

    assert cleaned[0]["translations"] == {"es": "Otra vez."}
