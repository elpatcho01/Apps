import Foundation
import FirebaseFirestore
import FirebaseFirestoreSwift

final class FirestoreService {

    private let db = Firestore.firestore()

    // MARK: - Collection reference

    private func notesCollection(for familyCode: String) -> CollectionReference {
        db.collection("spaces").document(familyCode).collection("notes")
    }

    // MARK: - Space management

    /// Creates or verifies the shared space exists in Firestore.
    func ensureSpace(familyCode: String) async throws {
        let spaceRef = db.collection("spaces").document(familyCode)
        let snapshot = try await spaceRef.getDocument()
        if !snapshot.exists {
            try await spaceRef.setData(["createdAt": FieldValue.serverTimestamp()])
        }
    }

    // MARK: - Real-time listener

    /// Attaches a snapshot listener and calls the handler whenever notes change.
    /// Returns a `ListenerRegistration` that must be removed to stop listening.
    func listenToNotes(
        familyCode: String,
        onChange: @escaping ([Note]) -> Void,
        onError: @escaping (Error) -> Void
    ) -> ListenerRegistration {
        notesCollection(for: familyCode)
            .addSnapshotListener { snapshot, error in
                if let error {
                    onError(error)
                    return
                }
                guard let documents = snapshot?.documents else { return }
                let notes = documents.compactMap { try? $0.data(as: Note.self) }
                onChange(notes)
            }
    }

    // MARK: - CRUD

    func addNote(_ note: Note, familyCode: String) async throws {
        _ = try notesCollection(for: familyCode).addDocument(from: note)
    }

    func updateNote(_ note: Note, familyCode: String) async throws {
        guard let id = note.id else { return }
        try notesCollection(for: familyCode).document(id).setData(from: note, merge: false)
    }

    func deleteNote(id: String, familyCode: String) async throws {
        try await notesCollection(for: familyCode).document(id).delete()
    }
}
