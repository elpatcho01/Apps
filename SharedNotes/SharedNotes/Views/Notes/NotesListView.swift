import SwiftUI

struct NotesListView: View {

    let user: AppUser
    @StateObject private var viewModel: NotesViewModel
    @State private var showingAddNote = false
    @State private var noteToEdit: Note?

    init(user: AppUser) {
        self.user = user
        _viewModel = StateObject(wrappedValue: NotesViewModel(user: user))
    }

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.notes.isEmpty {
                    EmptyStateView()
                } else {
                    notesList
                }
            }
            .navigationTitle("Shared Notes")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showingAddNote = true
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Text("Code: \(user.familyCode)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .sheet(isPresented: $showingAddNote) {
                NoteEditorView(mode: .create) { body in
                    viewModel.addNote(body: body)
                }
            }
            .sheet(item: $noteToEdit) { note in
                NoteEditorView(mode: .edit(note)) { body in
                    viewModel.updateNote(note, newBody: body)
                }
            }
            .alert("Error", isPresented: Binding(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.errorMessage = nil } }
            )) {
                Button("OK", role: .cancel) { viewModel.errorMessage = nil }
            } message: {
                Text(viewModel.errorMessage ?? "")
            }
        }
    }

    private var notesList: some View {
        List {
            ForEach(viewModel.notes) { note in
                NoteRowView(note: note, currentUserName: user.name)
                    .contentShape(Rectangle())
                    .onTapGesture { noteToEdit = note }
                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                        Button(role: .destructive) {
                            viewModel.deleteNote(note)
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                        Button {
                            noteToEdit = note
                        } label: {
                            Label("Edit", systemImage: "pencil")
                        }
                        .tint(.blue)
                    }
            }
        }
        .listStyle(.plain)
    }
}

#Preview {
    NotesListView(user: AppUser(name: "Alice", familyCode: "FAMILY"))
}
