from patchbay import create_remote_patch, Trigger, Slider

def trigger_func():
    exclamations = "!" * slider.value
    print("Yo"+exclamations)

patch = create_remote_patch(use_udp=False)

patch.bind(channel=1, event_handler=Trigger(trigger_func))
slider = patch.bind(channel=2, event_handler=Slider())

while True:
    patch.route_events()
