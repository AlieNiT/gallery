const apiKey = document.querySelector('meta[name="gallery-key"]').content;
const byId = (id) => document.getElementById(id);
const eventInput = byId('event-files');
const selfieInput = byId('selfie-file');
const uploadButton = byId('upload-button');
const searchButton = byId('search-button');
const dropZone = byId('drop-zone');
const uploadFeedback = byId('upload-feedback');
const searchFeedback = byId('search-feedback');

let selectedFiles = [];
let selfieUrl = null;
let facePage = 0;
let everyFace = false;

function feedback(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle('is-error', isError);
}

async function readJson(response) {
  let body;
  try { body = await response.json(); } catch { body = {}; }
  if (!response.ok) throw new Error(body.error || 'The request failed. Please try again.');
  return body;
}

async function sendFile(url, field, file) {
  const form = new FormData();
  form.append(field, file);
  return readJson(await fetch(url, { method: 'POST', headers: { 'X-Gallery-Key': apiKey }, body: form }));
}

function photoCard(photo, number) {
  const card = document.createElement('a');
  card.className = 'photo-card';
  card.href = `/api/photos/${photo.id}/original`;
  card.target = '_blank';
  card.rel = 'noopener noreferrer';
  card.title = `Open original: ${photo.name}`;

  const image = document.createElement('img');
  image.className = 'photo-image';
  image.src = `/api/photos/${photo.id}/preview`;
  image.alt = photo.name;
  image.loading = 'lazy';

  const meta = document.createElement('div');
  meta.className = 'photo-meta';
  const index = document.createElement('span');
  index.className = 'frame-number';
  index.textContent = String(number).padStart(2, '0');
  const details = document.createElement('span');
  const name = document.createElement('span');
  name.className = 'photo-name';
  name.textContent = photo.name;
  const date = document.createElement('span');
  date.className = 'photo-date';
  date.textContent = photo.captured_at ? `Taken ${photo.captured_at.slice(0, 10)}` : 'Date not recorded';
  details.append(name, date);
  meta.append(index, details);
  card.append(image, meta);
  return card;
}

function renderPhotos(photos, target) {
  target.replaceChildren(...photos.map((photo, index) => photoCard(photo, index + 1)));
}

async function refreshLibrary() {
  const data = await readJson(await fetch('/api/library'));
  byId('photo-count').textContent = data.stats.photos;
  byId('face-count').textContent = data.stats.faces;
  byId('group-count').textContent = data.stats.groups;
  renderPhotos(data.recent, byId('recent-grid'));
  byId('recent-count').textContent = data.recent.length ? `${data.recent.length} latest` : 'Add photos to begin';
}

function setSelectedFiles(files) {
  selectedFiles = Array.from(files);
  eventInput.value = '';
  uploadButton.disabled = selectedFiles.length === 0;
  const count = selectedFiles.length;
  byId('selection-line').textContent = count
    ? `${count} ${count === 1 ? 'photo' : 'photos'} selected · ${selectedFiles.slice(0, 2).map((file) => file.name).join(', ')}${count > 2 ? '…' : ''}`
    : 'No photos selected';
  feedback(uploadFeedback, '');
}

eventInput.addEventListener('change', (event) => setSelectedFiles(event.target.files));
['dragenter', 'dragover'].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.add('is-over');
}));
['dragleave', 'drop'].forEach((name) => dropZone.addEventListener(name, (event) => {
  event.preventDefault();
  dropZone.classList.remove('is-over');
}));
dropZone.addEventListener('drop', (event) => setSelectedFiles(event.dataTransfer.files));

uploadButton.addEventListener('click', async () => {
  if (!selectedFiles.length) return;
  uploadButton.disabled = true;
  const track = byId('upload-track');
  const bar = byId('upload-progress');
  track.hidden = false;
  let added = 0;
  let duplicates = 0;
  const failures = [];
  for (let index = 0; index < selectedFiles.length; index += 1) {
    const file = selectedFiles[index];
    feedback(uploadFeedback, `Indexing ${index + 1} of ${selectedFiles.length}: ${file.name}`);
    try {
      if (file.size > 50 * 1024 * 1024) throw new Error('File is over 50 MB');
      const result = await sendFile('/api/upload', 'photo', file);
      if (result.photo.duplicate) duplicates += 1;
      else added += 1;
    } catch (error) {
      failures.push(`${file.name}: ${error.message}`);
    }
    bar.style.width = `${Math.round(((index + 1) / selectedFiles.length) * 100)}%`;
  }
  const summary = [`${added} added`];
  if (duplicates) summary.push(`${duplicates} already in the library`);
  if (failures.length) summary.push(`${failures.length} failed (${failures[0]})`);
  feedback(uploadFeedback, summary.join(' · '), failures.length > 0);
  selectedFiles = [];
  byId('selection-line').textContent = 'No photos selected';
  await refreshLibrary();
});

selfieInput.addEventListener('change', () => {
  const file = selfieInput.files[0];
  searchButton.disabled = !file;
  if (selfieUrl) URL.revokeObjectURL(selfieUrl);
  if (!file) return;
  selfieUrl = URL.createObjectURL(file);
  const preview = byId('selfie-preview');
  preview.src = selfieUrl;
  preview.hidden = false;
  byId('selfie-placeholder').hidden = true;
  byId('selfie-choice').textContent = file.name;
  feedback(searchFeedback, '');
});

function showResults(photos, source) {
  const empty = byId('results-empty');
  const grid = byId('results-grid');
  byId('results-count').textContent = photos.length
    ? `${photos.length} possible ${photos.length === 1 ? 'photo' : 'photos'} · ${source}`
    : 'No confident matches';
  empty.hidden = photos.length > 0;
  grid.hidden = photos.length === 0;
  if (photos.length) renderPhotos(photos, grid);
  else empty.querySelector('p').textContent = 'No confident matches yet. Try another selfie, or browse detected faces below.';
  byId('results-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (!photos.length) showFaceBrowser();
}

searchButton.addEventListener('click', async () => {
  const file = selfieInput.files[0];
  if (!file) return;
  searchButton.disabled = true;
  feedback(searchFeedback, 'Looking through the library…');
  try {
    const result = await sendFile('/api/search', 'selfie', file);
    feedback(searchFeedback, result.photos.length ? 'Search complete. Matches are below.' : 'No match found. Browse faces below.');
    showResults(result.photos, 'selfie search');
  } catch (error) {
    feedback(searchFeedback, error.message, true);
    showFaceBrowser();
  } finally {
    searchButton.disabled = false;
  }
});

async function loadFaces() {
  const data = await readJson(await fetch(`/api/faces?page=${facePage}&all=${everyFace ? 1 : 0}`));
  const grid = byId('face-grid');
  const cards = data.faces.map((face) => {
    const button = document.createElement('button');
    button.className = 'face-card';
    button.type = 'button';
    button.setAttribute('aria-label', `Search using face ${face.id}`);
    const image = document.createElement('img');
    image.src = `/api/faces/${face.id}/thumbnail`;
    image.alt = '';
    image.loading = 'lazy';
    const label = document.createElement('span');
    label.textContent = `FACE ${String(face.id).padStart(3, '0')}`;
    button.append(image, label);
    button.addEventListener('click', async () => {
      feedback(searchFeedback, 'Looking for photos of that face…');
      try {
        const result = await readJson(await fetch(`/api/search/face/${face.id}`, {
          method: 'POST', headers: { 'X-Gallery-Key': apiKey },
        }));
        feedback(searchFeedback, result.photos.length ? 'Search complete. Matches are below.' : 'No match found.');
        showResults(result.photos, 'selected face');
      } catch (error) {
        feedback(searchFeedback, error.message, true);
      }
    });
    return button;
  });
  grid.replaceChildren(...cards);
  if (!cards.length) {
    const message = document.createElement('p');
    message.className = 'section-intro';
    message.textContent = 'No faces here yet. Add photos with clear faces to build this list.';
    grid.append(message);
  }
  byId('face-page-label').textContent = data.total ? `Page ${facePage + 1} of ${Math.ceil(data.total / 24)}` : 'No faces yet';
  byId('face-prev').disabled = facePage === 0;
  byId('face-next').disabled = (facePage + 1) * 24 >= data.total;
  byId('face-mode').textContent = everyFace ? 'Show face groups' : 'Show every detected face';
}

function showFaceBrowser() {
  byId('faces-section').hidden = false;
  loadFaces().catch((error) => feedback(searchFeedback, error.message, true));
}

byId('browse-button').addEventListener('click', () => {
  showFaceBrowser();
  byId('faces-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
byId('face-mode').addEventListener('click', () => { everyFace = !everyFace; facePage = 0; loadFaces(); });
byId('face-prev').addEventListener('click', () => { facePage -= 1; loadFaces(); });
byId('face-next').addEventListener('click', () => { facePage += 1; loadFaces(); });

refreshLibrary().catch(() => feedback(uploadFeedback, 'Could not load the library. Refresh the page.', true));
