from app import create_app

flask_app = create_app('tests/functional/config.test.yaml')


def test_pico_c_firmware():
    """
    GIVEN a Flask application configured for testing
    WHEN the '/API/pico/getFirmware' API is requested (GET)
    THEN check that the firmware response is valid
    """

    # Create a test client using the Flask application configured for testing
    with flask_app.test_client() as test_client:
        response = test_client.get('/API/pico/getFirmware?uid=unknown')
        assert response.status_code == 200
        # assert unknown device type doesn't get firmware loaded
        assert b"#F#" in response.data

        response = test_client.get('/API/pico/getFirmware?uid=picobrew_c_alt')
        assert response.status_code == 200
        # assert beginning of pico_c_0_1_34_alt.bin firmware file is in response
        assert b"#155864.0,72168d866990f8a46228df7113cb82ce|" in response.data
        
        response = test_client.get('/API/pico/getFirmware?uid=picobrew_c')
        assert response.status_code == 200
        # assert beginning of pico_c_0_1_34.bin firmware file is in response
        assert b"#156536.0,09a9cb94924d9023df6af18e4bed680f|" in response.data


def test_pico_recipe_changes_reach_devices_without_restart(tmp_path):
    """
    GIVEN a running server whose Pico recipe list was loaded at startup
    WHEN a Pico recipe is created, updated, then deleted through the UI endpoints
    THEN the device-facing /API/pico endpoints reflect each change immediately
    """
    import json
    app = create_app('tests/functional/config.test.yaml')
    for d in ['recipes/pico/archive', 'recipes/unified/archive']:
        (tmp_path / d).mkdir(parents=True)
    app.config.update(RECIPES_PATH=tmp_path / 'recipes')
    step = {'name': 'Mash', 'location': 'Mash', 'temperature': 152, 'step_time': 30, 'drain_time': 0}

    with app.test_client() as client:
        assert client.post('/new_pico_recipe', json={'name': 'Fresh Pale', 'abv': 5, 'ibu': 30,
                                                      'image': '', 'steps': [step]}).status_code == 204
        recipe_id = json.loads(next((tmp_path / 'recipes/pico').glob('Fresh_Pale.json')).read_text())['id']

        paks = client.get('/API/pico/getAssociatedPaks?uid=test').get_data(as_text=True)
        assert f'{recipe_id},Fresh Pale|' in paks
        served = client.get(f'/API/pico/getRecipe?uid=test&rfid={recipe_id}&ibu=-1&abv=-1').get_data(as_text=True)
        assert '152,30,0,1,Mash,' in served

        client.post('/update_pico_recipe', json={'id': recipe_id, 'name': 'Fresh Pale', 'steps': [dict(step, temperature=150)]})
        served = client.get(f'/API/pico/getRecipe?uid=test&rfid={recipe_id}&ibu=-1&abv=-1').get_data(as_text=True)
        assert '150,30,0,1,Mash,' in served

        assert client.post('/delete_pico_recipe', json=recipe_id).status_code == 204
        assert recipe_id not in client.get('/API/pico/getAssociatedPaks?uid=test').get_data(as_text=True)
        assert client.get(f'/API/pico/getRecipe?uid=test&rfid={recipe_id}&ibu=-1&abv=-1').get_data(as_text=True) == '##'
