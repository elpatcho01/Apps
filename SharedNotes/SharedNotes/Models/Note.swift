import Foundation
import FirebaseFirestore
import FirebaseFirestoreSwift

struct Note: Identifiable, Codable {
    @DocumentID var id: String?
    var body: String
    var authorName: String
    var createdAt: Date
    var updatedAt: Date
}
