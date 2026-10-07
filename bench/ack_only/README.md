# Bench-only ACK sketch (temporary)

For the Pi test rig only. Lets the UNO Q acknowledge the Pi's frames. It has no
send call. NEVER flash it onto a board connected to a boat.

Deploy (from the repo root; board is `arduino@192.168.201.129`):

    (echo '#define BENCH_ONLY_ACK 1'; cat bench/ack_only/sketch.ino.bench) > /tmp/bench_sketch.ino
    scp /tmp/bench_sketch.ino arduino@192.168.201.129:ArduinoApps/unoq_nmea2000_capture/sketch/sketch.ino
    ssh arduino@192.168.201.129 'arduino-app-cli app restart user:unoq_nmea2000_capture'

ACK mode needs a jumper wire from D6 to GND at boot. Without it the board acts
like production (listen-only, silent pin HIGH).

Restore production (do this when finished):

    scp firmware/unoq_nmea2000_capture/sketch/sketch.ino arduino@192.168.201.129:ArduinoApps/unoq_nmea2000_capture/sketch/sketch.ino
    ssh arduino@192.168.201.129 'arduino-app-cli app restart user:unoq_nmea2000_capture'
    # then remove the D6 jumper

Check which one is running: the health line's controller mode is `Normal20B`
(bench ACK) or `ListenOnly` (production).
