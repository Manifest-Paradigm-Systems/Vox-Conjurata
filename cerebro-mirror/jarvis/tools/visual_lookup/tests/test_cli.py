import pytest
from unittest.mock import patch
from click.testing import CliRunner
from visual_lookup.cli import main

@patch('visual_lookup.cli.query_vision')
@patch('visual_lookup.cli.read_image')
def test_cli(mock_read_image, mock_query_vision):
    mock_read_image.return_value = 'base64_image'
    mock_query_vision.return_value = '{"markings": ["marking1"], "description": "description"}'

    runner = CliRunner()
    result = runner.invoke(main, ['image.jpg', 'What is this?'])

    assert result.exit_code == 0

@patch('visual_lookup.cli.query_vision')
@patch('visual_lookup.cli.read_image')
def test_cli_missing_image(mock_read_image, mock_query_vision):
    mock_read_image.side_effect = FileNotFoundError

    runner = CliRunner()
    result = runner.invoke(main, ['nonexistent_image.jpg', 'What is this?'])

    assert result.exit_code != 0
