# app.py - Main application file
import os
import sys
import time
import threading
import re
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QPushButton, QLineEdit, QLabel, 
                            QProgressBar, QScrollArea, QFileDialog, QFrame,
                            QMessageBox)
from PyQt5.QtCore import Qt, QObject, QRunnable, QThreadPool, pyqtSignal, QUrl
from PyQt5.QtGui import QPixmap, QDesktopServices
import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from pytube import YouTube
from pytube.exceptions import VideoUnavailable
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

# Create downloads directory if it doesn't exist
DOWNLOADS_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "SpotifyToMP3")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(os.path.join(DOWNLOADS_DIR, "Track"), exist_ok=True)
os.makedirs(os.path.join(DOWNLOADS_DIR, "Playlist"), exist_ok=True)
os.makedirs(os.path.join(DOWNLOADS_DIR, "Album"), exist_ok=True)

class WorkerSignals(QObject):
    progress_updated = pyqtSignal(str, int)
    download_finished = pyqtSignal(str, str)
    download_error = pyqtSignal(str, str)

class DownloadWorker(QRunnable):
    # progress_updated = pyqtSignal(str, int)
    # download_finished = pyqtSignal(str, str)
    # download_error = pyqtSignal(str, str)
    
    def __init__(self, track_id, track_info, download_dir):
        super().__init__()
        self.track_id = track_id
        self.track_info = track_info
        self.download_dir = download_dir
        self.signals = WorkerSignals()
        self.stopped = False
        
    def run(self):
        try:
            # Search for the track on YouTube
            search_query = f"{self.track_info['artist']} - {self.track_info['title']}"
            # Sanitize filename
            safe_filename = "".join([c for c in f"{self.track_info['artist']} - {self.track_info['title']}" if c.isalnum() or c in (' ', '-', '_')]).rstrip()
            output_file = os.path.join(self.download_dir, f"{safe_filename}.mp3")
            
            # If the file already exists, we can consider it completed and skip it.
            if os.path.exists(output_file):
                self.signals.progress_updated.emit(self.track_id, 100)
                self.signals.download_finished.emit(self.track_id, output_file)
                return 
            
            self.signals.progress_updated.emit(self.track_id, 10)
            
            
            # Use yt-dlp to search and find the best match
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'default_search': 'ytsearch1',
                'format': 'bestaudio/best',
                'noplaylist': True,
                'ignoreerrors': True, 
                'retries': 5,           
                'fragment_retries': 5,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'progress_hooks': [self._progress_hook],
                'outtmpl': os.path.join(self.download_dir, f"{safe_filename}.%(ext)s")
            }
            
            self.signals.progress_updated.emit(self.track_id, 20)
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Find the video
                info = ydl.extract_info(search_query, download=False)
                # Check if any video was found
                if not info.get('entries'):
                    raise VideoUnavailable("No search results found on YouTube.")
                video_item = info['entries'][0]
                self.track_info['thumbnail'] = video_item.get('thumbnail', '')
                self.track_info['youtube_url'] = video_item.get('webpage_url', '')
                
                self.signals.progress_updated.emit(self.track_id, 30)
                
                if self.stopped:
                    return
                    
                # Download
                ydl.download([video_item['webpage_url']])
                
            # output_file = os.path.join(self.download_dir, f"{safe_filename}.mp3")
            # self.signals.download_finished.emit(self.track_id, output_file)
            if os.path.exists(output_file) and os.path.getsize(output_file) > 1024:
                self.signals.download_finished.emit(self.track_id, output_file)
            else:
                raise Exception("File is empty or missing (skipped by downloader).")
            
        except Exception as e:
            self.signals.download_error.emit(self.track_id, str(e))
        finally:
            time.sleep(1)
    
    def _progress_hook(self, d):
        if self.stopped:
            return
        
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%') # Progress percentage
            try:
                percentage = int(float(p.strip('%')))
                scaled_percentage = 30 + int(percentage * 0.6)
                self.signals.progress_updated.emit(self.track_id, scaled_percentage)
            except:
                pass
        elif d['status'] == 'finished':
            self.signals.progress_updated.emit(self.track_id, 90)
    
    def stop(self):
        self.stopped = True

class SpotifyClient:
    def __init__(self):
        client_credentials_manager = SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        )
        self.sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
    
    def is_playlist(self, url):
        return 'playlist' in url
    
    def is_album(self, url):
        return 'album' in url
    
    def is_track(self, url):
        return 'track' in url
    
    def get_playlist_name(self, playlist_url):
        playlist_id = playlist_url.split('/')[-1].split('?')[0]
        results = self.sp.playlist(playlist_id)
        return results['name']
    
    def get_album_name(self, album_url):
        album_id = album_url.split('/')[-1].split('?')[0]
        results = self.sp.album(album_id)
        return results['name']
    
    def get_tracks_from_playlist(self, playlist_url):
        playlist_id = playlist_url.split('/')[-1].split('?')[0]
        results = self.sp.playlist_tracks(playlist_id)
        tracks = []
        
        # for item in results['tracks']['items']:
        while results:
            for item in results['items']:
                track = item['track']
                if track:
                    tracks.append({
                        'title': track['name'],
                        'artist': track['artists'][0]['name'],
                        'id': track['id'],
                        'album': track['album']['name'],
                        'duration_ms': track['duration_ms'],
                        'spotify_url': track['external_urls']['spotify']
                    })
            if results['next']:
                results = self.sp.next(results)
            else:
                results = None
            
        return tracks
    
    def get_tracks_from_album(self, album_url):
        album_id = album_url.split('/')[-1].split('?')[0]
        album_info = self.sp.album(album_id)
        if not album_info:
            return []
        
        results = self.sp.album_tracks(album_id)
        tracks = []
        
        # for item in results['tracks']['items']:
        while results:
            for item in results['items']:
                if item:
                    track_spotify_url = f"https://open.spotify.com/track/{item['id']}"
                    tracks.append({
                        'title': item['name'],
                        'artist': item['artists'][0]['name'],
                        'id': item['id'],
                        'album': album_info['name'],
                        'duration_ms': item['duration_ms'],
                        'spotify_url': track_spotify_url
                    })
            if results['next']:
                results = self.sp.next(results)
            else:
                results = None
            
        return tracks
    
    def get_track(self, track_url):
        track_id = track_url.split('/')[-1].split('?')[0]
        track = self.sp.track(track_id)
        return {
            'title': track['name'],
            'artist': track['artists'][0]['name'],
            'id': track['id'],
            'album': track['album']['name'],
            'duration_ms': track['duration_ms'],
            'spotify_url': track['external_urls']['spotify']
        }

class DownloadCard(QFrame):
    def __init__(self, track_id, track_info, parent=None):
        super().__init__(parent)
        self.track_id = track_id
        self.track_info = track_info
        self.file_path = ""
        
        self.setStyleSheet("""
            QFrame {
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                background-color: #f5f5f5;
                margin: 3px;
            }
        """)
        
        self.setFixedHeight(90)
        
        layout = QHBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12) 
        layout.setSpacing(15)
        
        # Container for thumbnail
        thumbnail_container = QVBoxLayout()
        thumbnail_container.setContentsMargins(0, 0, 0, 0)
        thumbnail_container.setAlignment(Qt.AlignCenter)
        
        # Thumbnail area
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(60, 60)
        self.thumbnail_label.setStyleSheet("""
            background-color: #e0e0e0; 
            border-radius: 4px;
        """)
        self.thumbnail_label.setAlignment(Qt.AlignCenter)
        self.thumbnail_label.setScaledContents(True)
        self.load_thumbnail()
        
        thumbnail_container.addWidget(self.thumbnail_label)
        
        # Info area
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)  # More space between title and progress bar
        info_layout.setContentsMargins(0, 2, 0, 0)  # No margins inside info layout
        
        # Title and artist
        self.title_label = QLabel(f"{track_info['title']} - {track_info['artist']}")
        self.title_label.setStyleSheet("""
            font-size: 14px;
            color: #333333;
            border: none;
            padding: 0px;  /* No padding to ensure perfect alignment */
        """)
        self.title_label.setWordWrap(False)
        self.title_label.setFixedHeight(25)  # Ensure enough height for text with descenders
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 6px;
                background-color: #e0e0e0;
                text-align: center;
                font-size: 12px;
                margin: 0px;  /* No margins to ensure perfect alignment */
                padding: 0px;
            }
            QProgressBar::chunk {
                background-color: #1DB954;
                border-radius: 6px;
            }
        """)
        
        # Widgets
        info_layout.addWidget(self.title_label)
        info_layout.addWidget(self.progress_bar)
        info_layout.addStretch()  # Push everything to the top
        
        layout.addLayout(thumbnail_container)
        layout.addLayout(info_layout, 1)
        
        self.setLayout(layout)
    
    def load_thumbnail(self):
        if 'thumbnail' in self.track_info and self.track_info['thumbnail']:
            try:
                response = requests.get(self.track_info['thumbnail'])
                pixmap = QPixmap()
                pixmap.loadFromData(response.content)
                self.thumbnail_label.setPixmap(pixmap)
            except Exception:
                self.thumbnail_label.setText("No Image")
        else:
            self.thumbnail_label.setText("Loading...")
    
    def set_progress(self, progress):
        self.progress_bar.setValue(progress)
        self.progress_bar.setFormat("%p%") # Show percentage
    
    def set_completed(self, file_path):
        # self.file_path = file_path
        # self.set_progress(100)
        self.file_path = file_path
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("Completed") # Set the text to "Completed"
    
    def set_error(self, error_message):
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat("Error") # Show error text
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 3px;
                background-color: #e0e0e0;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #E74C3C;
                border-radius: 3px;
            }
        """)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("Spotify to MP3 Downloader")
        self.setMinimumSize(650, 500)
        self.spotify_client = SpotifyClient()
        self.download_workers = {}
        self.download_cards = {}
        self.threadpool = QThreadPool()
        self.threadpool.setMaxThreadCount(4) 
        self.active_download_count = 0
        
        # Set application style
        self.setStyleSheet("""
            QMainWindow {
                background-color: #ffffff;
            }
            QScrollArea {
                border: none;
                background-color: #ffffff;
            }
            QLabel {
                color: #333333;
            }
        """)
        
        # Central widget
        central_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # App title
        title_label = QLabel("Spotify to MP3")
        title_label.setStyleSheet("""
            font-size: 24px;
            font-weight: bold;
            color: #1DB954;
            margin-bottom: 10px;
        """)
        
        # Top input area
        input_layout = QHBoxLayout()
        input_layout.setSpacing(10)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter Spotify URL (track, playlist, or album)")
        self.url_input.setStyleSheet("""
            QLineEdit {
                padding: 12px;
                border: 2px solid #e0e0e0;
                border-radius: 8px;
                background-color: white;
                font-size: 14px;
                color: #333333;
            }
            QLineEdit:focus {
                border-color: #1DB954;
            }
        """)
        
        self.download_btn = QPushButton("Download")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #1DB954;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 20px;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #1ED760;
            }
            QPushButton:pressed {
                background-color: #1AA246;
            }
        """)
        self.download_btn.clicked.connect(self.process_url)
        
        input_layout.addWidget(self.url_input, 4)
        input_layout.addWidget(self.download_btn, 1)
        
        # Header for downloads section
        downloads_header = QLabel("Downloads")
        downloads_header.setStyleSheet("""
            font-size: 18px;
            font-weight: bold;
            color: #333333;
            margin-top: 10px;
        """)
        
        # Scroll area for downloads
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: white;
            }
            QScrollBar:vertical {
                border: none;
                background: #f0f0f0;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #c0c0c0;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: #a0a0a0;
            }
            
            QScrollBar:horizontal {
                border: none;
                background: #f0f0f0;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal {
                background: #c0c0c0;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #a0a0a0;
            }
        """)
        
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: white;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setAlignment(Qt.AlignTop)
        self.scroll_layout.setContentsMargins(10, 10, 10, 10)
        self.scroll_layout.setSpacing(0)
        self.scroll_area.setWidget(self.scroll_content)
        
        # Status bar
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("""
            font-size: 13px;
            color: #666666;
            padding: 8px;
            border-top: 1px solid #e0e0e0;
        """)
        
        main_layout.addWidget(title_label)
        main_layout.addLayout(input_layout)
        main_layout.addWidget(downloads_header)
        main_layout.addWidget(self.scroll_area)
        main_layout.addWidget(self.status_label)
        
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)
    
    def process_url(self):
        url = self.url_input.text().strip()
        if not url:
            self.show_error("Please enter a valid Spotify URL")
            return
            
        # self.status_label.setText("Processing URL...")
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #E74C3C;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 12px 20px;
                font-weight: bold;
                font-size: 14px;
            }
        """)
        
        try:
            # Sanitize folder name helper function
            def sanitize_folder_name(name):
                return "".join([c for c in name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
            
            if self.spotify_client.is_playlist(url):
                self.status_label.setText("Fetching playlist tracks...")
                playlist_name = self.spotify_client.get_playlist_name(url)
                folder_name = sanitize_folder_name(playlist_name)
                tracks = self.spotify_client.get_tracks_from_playlist(url)
                download_dir = os.path.join(DOWNLOADS_DIR, "Playlist", folder_name)
            elif self.spotify_client.is_album(url):
                self.status_label.setText("Fetching album tracks...")
                album_name = self.spotify_client.get_album_name(url)
                folder_name = sanitize_folder_name(album_name)
                tracks = self.spotify_client.get_tracks_from_album(url)
                download_dir = os.path.join(DOWNLOADS_DIR, "Album", folder_name)
            elif self.spotify_client.is_track(url):
                self.status_label.setText("Fetching track...")
                tracks = [self.spotify_client.get_track(url)]
                download_dir = os.path.join(DOWNLOADS_DIR, "Track")
            else:
                self.show_error("Invalid Spotify URL. Please enter a track, playlist, or album URL.")
                self.download_btn.setEnabled(True)
                self.download_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #1DB954;
                        color: white;
                        border: none;
                        border-radius: 8px;
                        padding: 12px 20px;
                        font-weight: bold;
                        font-size: 14px;
                    }
                    QPushButton:hover {
                        background-color: #1ED760;
                    }
                    QPushButton:pressed {
                        background-color: #1AA246;
                    }
                """)
                return
            
            os.makedirs(download_dir, exist_ok=True)
            
            self.status_label.setText(f"Found {len(tracks)} track(s). Starting download...")
            self.active_download_count = len(tracks)
            
            for track in tracks:
                self.add_download_task(track, download_dir)
            
        except Exception as e:
            #self.show_error(f"Error processing URL: {str(e)}")
            self.show_error("Invalid Spotify URL. Please enter a track, playlist, or album URL.")
            self.download_btn.setEnabled(True)
            self.download_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1DB954;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 20px;
                    font-weight: bold;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background-color: #1ED760;
                }
                QPushButton:pressed {
                    background-color: #1AA246;
                }
            """)
        
        # self.download_btn.setEnabled(True)
    
    def add_download_task(self, track, download_dir):
        track_id = track['id']
        
        # Create card
        card = DownloadCard(track_id, track)
        self.download_cards[track_id] = card
        
        # Add new cards at the top
        # if self.scroll_layout.count() > 0:
        #     self.scroll_layout.insertWidget(0, card)
        # else:
        #     self.scroll_layout.addWidget(card)
        self.scroll_layout.insertWidget(0, card)
        
        # Create worker
        worker = DownloadWorker(track_id, track, download_dir)
        worker.signals.progress_updated.connect(self.update_progress)
        worker.signals.download_finished.connect(self.download_completed)
        worker.signals.download_error.connect(self.download_error)
        
        self.download_workers[track_id] = worker
        self.threadpool.start(worker) # worker.start()
    
    def update_progress(self, track_id, progress):
        if track_id in self.download_cards:
            self.download_cards[track_id].set_progress(progress)
            
            # Update thumbnail if available
            if progress >= 30:
                self.download_cards[track_id].load_thumbnail()
    
    def download_completed(self, track_id, file_path):
        if track_id in self.download_cards:
            self.download_cards[track_id].set_completed(file_path)
        
        if self.active_download_count > 0:
            self.active_download_count -= 1
        
        self.check_all_completed()
    
    def download_error(self, track_id, error_message):
        if track_id in self.download_cards:
            self.download_cards[track_id].set_error(error_message)
        
        if self.active_download_count > 0:
            self.active_download_count -= 1
        
        self.check_all_completed()
    
    def check_all_completed(self):
        # all_completed = True
        # active_downloads = 0
        
        # for worker in self.download_workers.values():
        #     if worker.isRunning():
        #         all_completed = False
        #         active_downloads += 1
        
        # if all_completed or active_downloads == 0:
        if self.active_download_count == 0:
            self.status_label.setText("All downloads completed")
            self.download_btn.setEnabled(True)
            self.download_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1DB954;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 20px;
                    font-weight: bold;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background-color: #1ED760;
                }
                QPushButton:pressed {
                    background-color: #1AA246;
                }
            """)
        else:
            self.status_label.setText(f"{self.active_download_count} downloads remaining")
    
    def show_error(self, message):
        # Display error message only in the status label
        self.status_label.setText(f"Error: {message}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())