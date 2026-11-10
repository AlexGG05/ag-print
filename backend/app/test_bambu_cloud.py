from app.bambu_cloud import CloudMonitor

def test_merges_partial_telemetry_and_ams():
    m=CloudMonitor()
    m._merge({'mc_percent':30,'ams':{'ams':[{'id':'0','tray':[{'id':'0','tray_type':'PLA','tray_color':'FF0000FF','remain':85},{'id':'3','tray_type':'PETG'}]}]}})
    m._merge({'nozzle_temper':204.5})
    s=m.snapshot()
    assert s['progress_percent']==30 and s['nozzle_temp']==204.5
    assert [x['slot'] for x in s['ams']]==[1,4]
    assert s['ams'][0]['material']=='PLA'
    assert s['ams'][0]['color_hex']=='FF0000FF'

def test_disconnected_cannot_publish():
    m=CloudMonitor()
    try:
        m.publish_command('print','pause')
        assert False, 'expected connection error'
    except ConnectionError:
        pass
