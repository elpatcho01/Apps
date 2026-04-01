# SharedNotes

A simple iOS app for sharing notes in real-time between two people.

## Features

- Real-time sync — notes appear on both phones within seconds
- No accounts needed — just a shared 6-character code
- Create, edit, and delete notes
- Shows who wrote each note and when

---

## Setup (one-time, ~10 minutes)

### 1. Firebase Project

1. Go to [console.firebase.google.com](https://console.firebase.google.com)
2. Click **Add project** → name it "SharedNotes" → disable Google Analytics → **Create project**
3. Click the **iOS** icon to add an iOS app
4. Enter Bundle ID: `com.yourname.SharedNotes` (must match `project.yml`)
5. Download **GoogleService-Info.plist** and place it at:
   ```
   SharedNotes/GoogleService-Info.plist
   ```
6. Skip the remaining Firebase setup steps (SDK is added via SPM below)

### 2. Firestore Database

1. In the Firebase Console sidebar: **Build → Firestore Database**
2. Click **Create database** → choose your region → **Start in production mode**
3. Go to the **Rules** tab and paste the contents of `firestore.rules`, then **Publish**

### 3. Generate the Xcode Project

Option A — XcodeGen (recommended):
```bash
brew install xcodegen
cd SharedNotes
xcodegen generate
open SharedNotes.xcodeproj
```

Option B — Manual:
1. Open Xcode → **File → New → Project** → App
2. Set Product Name to `SharedNotes`, Bundle ID to `com.yourname.SharedNotes`, minimum iOS 16
3. Add all `.swift` files from the `SharedNotes/` folder
4. Add Firebase SDK: **File → Add Package Dependencies**
   - URL: `https://github.com/firebase/firebase-ios-sdk`
   - Version: Up to Next Major from `11.0.0`
   - Select products: **FirebaseFirestore** and **FirebaseFirestoreSwift**
5. Add `GoogleService-Info.plist` to the project (check "Copy items if needed")

### 4. Run

1. Select your iPhone as the target device
2. Press **⌘R** to build and run

---

## Using the App

**First launch (both phones):**
1. Enter your name
2. Enter the same 6-character code on both phones (e.g. `FAMILY`)
3. Tap **Get Started**

**Writing notes:**
- Tap the pencil icon (top right) to create a new note
- Tap any note to edit it
- Swipe left on a note to delete or edit it

---

## How It Works

```
Firestore structure:
  spaces/
    {FAMILY_CODE}/          ← shared by both phones
      notes/
        {note_id}/
          body: "..."
          authorName: "Alice"
          createdAt: timestamp
          updatedAt: timestamp
```

Both phones listen to the same Firestore path in real-time. Changes sync in under 1 second on a normal connection. The app also works offline — changes are queued and sync when connectivity returns.

---

## Privacy

- Notes are stored in Firebase (Google's servers)
- No account or email required
- The family code is the only access control — keep it private
- Free tier (Spark plan) supports up to 1 GB storage and 50,000 reads/day, which is more than enough for personal use

## Customization

To change the bundle ID or team, edit `project.yml` before running `xcodegen`.
