import SwiftUI

struct NoteEditorView: View {

    enum Mode {
        case create
        case edit(Note)
    }

    let mode: Mode
    let onSave: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var body: String = ""
    @FocusState private var isFocused: Bool

    private var isEditing: Bool {
        if case .edit = mode { return true }
        return false
    }

    private var title: String { isEditing ? "Edit Note" : "New Note" }

    init(mode: Mode, onSave: @escaping (String) -> Void) {
        self.mode = mode
        self.onSave = onSave
        if case .edit(let note) = mode {
            _body = State(initialValue: note.body)
        }
    }

    var body: some View {
        NavigationStack {
            TextEditor(text: $body)
                .font(.body)
                .padding()
                .focused($isFocused)
                .navigationTitle(title)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { dismiss() }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") {
                            let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
                            guard !trimmed.isEmpty else { return }
                            onSave(trimmed)
                            dismiss()
                        }
                        .disabled(body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        .fontWeight(.semibold)
                    }
                }
        }
        .onAppear { isFocused = true }
    }
}

#Preview {
    NoteEditorView(mode: .create) { _ in }
}
