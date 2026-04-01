import Foundation

enum LocalStore {
    static let nameKey = "sharedNotes.userName"
    static let codeKey = "sharedNotes.familyCode"

    static func clear() {
        UserDefaults.standard.removeObject(forKey: nameKey)
        UserDefaults.standard.removeObject(forKey: codeKey)
    }
}
