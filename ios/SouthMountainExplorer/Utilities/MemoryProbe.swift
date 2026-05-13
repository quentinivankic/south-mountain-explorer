import Foundation
import Darwin

/// Reads the app process's resident memory footprint via Mach
/// task info — the same metric Xcode's debug navigator displays
/// as "Memory". Used by the debug HUD to surface memory pressure
/// in the field without having to attach a debugger.
///
/// Cheap to call (a syscall, no allocations beyond the response
/// struct). The HUD polls once per second, which is the
/// resolution Xcode itself samples at.
enum MemoryProbe {
    /// Resident footprint in megabytes. Returns 0 if the Mach
    /// task-info call fails (shouldn't happen in practice — the
    /// call is well-supported on every iOS version this app
    /// targets — but the HUD shouldn't crash if it ever does).
    static func footprintMB() -> Double {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(
            MemoryLayout<mach_task_basic_info>.size / MemoryLayout<integer_t>.size
        )
        let result: kern_return_t = withUnsafeMutablePointer(to: &info) { infoPtr in
            infoPtr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { reboundPtr in
                task_info(
                    mach_task_self_,
                    task_flavor_t(MACH_TASK_BASIC_INFO),
                    reboundPtr,
                    &count
                )
            }
        }
        guard result == KERN_SUCCESS else { return 0 }
        return Double(info.resident_size) / (1024 * 1024)
    }
}
