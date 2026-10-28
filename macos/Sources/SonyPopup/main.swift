import AppKit

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)   // bez ikony w Docku, tylko menu bar + notch
app.run()
