import Foundation
import FirebaseFirestore

@MainActor
final class NotesViewModel: ObservableObject {

    @Published var notes: [Note] = []
    @Published var errorMessage: String?

    private let user: AppUser
    private let service = FirestoreService()
    private var listener: ListenerRegistration?

    init(user: AppUser) {
        self.user = user
        startListening()
    }

    deinit {
        listener?.remove()
    }

    // MARK: - Real-time listener

    private func startListening() {
        listener = service.listenToNotes(
            familyCode: user.familyCode,
            onChange: { [weak self] notes in
                self?.notes = notes.sorted { $0.updatedAt > $1.updatedAt }
            },
            onError: { [weak self] error in
                self?.errorMessage = error.localizedDescription
            }
        )
    }

    // MARK: - CRUD

    func addNote(body: String) {
        let now = Date()
        let note = Note(
            body: body,
            authorName: user.name,
            createdAt: now,
            updatedAt: now
        )
        Task {
            do {
                try await service.addNote(note, familyCode: user.familyCode)
            } catch {
                errorMessage = "Failed to save note."
            }
        }
    }

    func updateNote(_ note: Note, newBody: String) {
        var updated = note
        updated.body = newBody
        updated.updatedAt = Date()

        // Optimistic update
        if let index = notes.firstIndex(where: { $0.id == note.id }) {
            notes[index] = updated
            notes.sort { $0.updatedAt > $1.updatedAt }
        }

        Task {
            do {
                try await service.updateNote(updated, familyCode: user.familyCode)
            } catch {
                errorMessage = "Failed to update note."
            }
        }
    }

    func deleteNote(_ note: Note) {
        guard let id = note.id else { return }

        // Optimistic delete
        notes.removeAll { $0.id == id }

        Task {
            do {
                try await service.deleteNote(id: id, familyCode: user.familyCode)
            } catch {
                errorMessage = "Failed to delete note."
            }
        }
    }
}
