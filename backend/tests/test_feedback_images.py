import base64
import pytest

from src.core.feedback_images import save_feedback_images, validate_feedback_images


def data_url(mime, content):
    return f'data:{mime};base64,' + base64.b64encode(content).decode()


PNG = data_url('image/png', b'\x89PNG\r\n\x1a\nbody')


def test_images_are_deduplicated_and_saved_by_hash(tmp_path):
    db = str(tmp_path / 'app.db')
    names = save_feedback_images(db, [PNG, PNG])
    assert len(names) == 1 and names[0].endswith('.png')
    assert (tmp_path / 'feedback_images' / names[0]).read_bytes().startswith(b'\x89PNG')
    assert save_feedback_images(db, None) == []


@pytest.mark.parametrize('image', [
    'data:image/svg+xml;base64,AAAA',
    'https://example.com/a.png',
    data_url('image/png', b'not a png'),
    data_url('image/webp', b'RIFF0000WAVE'),
    data_url('image/png', b'\x89PNG\r\n\x1a\n' + b'x' * 5_000_000),
])
def test_invalid_images_are_rejected(image):
    with pytest.raises(ValueError):
        validate_feedback_images([image])


def test_too_many_images_are_rejected():
    with pytest.raises(ValueError, match='最多'):
        validate_feedback_images([PNG] * 5)
